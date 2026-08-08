"""Gemini wrapper shared by all agents.

Free-tier survival kit: responses are cached on disk, and when a model runs out
of daily quota we fall through to the next one (quota is per model).
"""

import hashlib
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

FALLBACKS = [
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-flash-lite-latest",
    "gemini-2.0-flash",
]
CACHE_DIR = Path(".llm_cache")


def unwrap(raw, key: str) -> dict:
    """Models sometimes return a bare array where an object was asked for."""
    if isinstance(raw, list):
        return {key: raw}
    return raw if isinstance(raw, dict) else {}


def _dead_end(err: Exception) -> bool:
    """Out of quota or model unavailable - retrying this model is pointless."""
    text = str(err)
    return any(s in text for s in ("RESOURCE_EXHAUSTED", "429", "NOT_FOUND", "404"))


class LLM:
    def __init__(self, model: str | None = None, use_cache: bool = True):
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY missing - copy .env.example to .env")
        self.client = genai.Client(api_key=key)
        self.model = model or os.getenv("GEMINI_MODEL", FALLBACKS[0])
        self.use_cache = use_cache
        # configured model first, then the rest as backups
        self.chain = [self.model] + [m for m in FALLBACKS if m != self.model]

    def ask(self, prompt: str, system: str = "", retries: int = 3) -> str:
        return self._call(prompt, system, as_json=False, retries=retries)

    def ask_json(self, prompt: str, system: str = "", retries: int = 3) -> dict | list:
        return json.loads(self._call(prompt, system, as_json=True, retries=retries))

    def _cache_path(self, prompt: str, system: str, as_json: bool) -> Path:
        # model is deliberately not part of the key, so a fallback still hits cache
        digest = hashlib.sha1(f"{system}|{as_json}|{prompt}".encode()).hexdigest()[:16]
        return CACHE_DIR / f"{digest}.txt"

    def _call(self, prompt: str, system: str, as_json: bool, retries: int) -> str:
        cache = self._cache_path(prompt, system, as_json)
        if self.use_cache and cache.exists():
            return cache.read_text(encoding="utf-8")

        cfg = types.GenerateContentConfig(
            system_instruction=system or None,
            response_mime_type="application/json" if as_json else None,
        )

        last = None
        for model in self.chain:
            for attempt in range(retries):
                try:
                    resp = self.client.models.generate_content(
                        model=model, contents=prompt, config=cfg
                    )
                    if model != self.model:
                        print(f"    (fell back to {model})")
                    if self.use_cache:
                        CACHE_DIR.mkdir(exist_ok=True)
                        cache.write_text(resp.text, encoding="utf-8")
                    return resp.text
                except Exception as e:
                    last = e
                    if _dead_end(e):
                        break  # move on to the next model
                    time.sleep(2**attempt)
        raise RuntimeError(f"All models failed. Last error: {last}")

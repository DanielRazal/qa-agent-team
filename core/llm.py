"""Gemini wrapper shared by all agents."""

import json
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


class LLM:
    def __init__(self, model: str | None = None):
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY missing - copy .env.example to .env")
        self.client = genai.Client(api_key=key)
        self.model = model or _MODEL

    def ask(self, prompt: str, system: str = "", retries: int = 3) -> str:
        """Plain text answer."""
        cfg = types.GenerateContentConfig(system_instruction=system) if system else None
        return self._call(prompt, cfg, retries)

    def ask_json(self, prompt: str, system: str = "", retries: int = 3) -> dict | list:
        """Answer forced into valid JSON."""
        cfg = types.GenerateContentConfig(
            system_instruction=system or None,
            response_mime_type="application/json",
        )
        return json.loads(self._call(prompt, cfg, retries))

    def _call(self, prompt: str, cfg, retries: int) -> str:
        last = None
        for attempt in range(retries):
            try:
                resp = self.client.models.generate_content(
                    model=self.model, contents=prompt, config=cfg
                )
                return resp.text
            except Exception as e:  # quota / transient errors
                last = e
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Gemini failed after {retries} tries: {last}")

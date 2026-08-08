"""Sanity check: is the environment ready?"""

from core.llm import LLM

llm = LLM()
print(f"Model: {llm.model}")
print("Reply:", llm.ask("Answer with one word: ready?"))

data = llm.ask_json('Return JSON: {"status": "ok"}')
print("JSON mode:", data)

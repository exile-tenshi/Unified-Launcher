# Pluggable assistant chat providers (Groq primary, Gemini optional).
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore


def default_system_prompt() -> str:
    return (
        "You are Tenshi, the AI assistant for the Tenshi Network — a secure, encrypted social platform "
        "for gamers and creators. You are friendly, helpful, and concise unless asked otherwise.\n"
        "Strictly follow platform safety: stay SFW, never assist with illegality/harm/doxxing."
    )


class BaseProvider:
    name: str = "base"

    def chat_completion(
        self, *, system: str, history: List[Dict[str, str]], model_hint: Optional[str] = None
    ) -> Optional[str]:
        raise NotImplementedError


class GroqProvider(BaseProvider):
    name = "groq"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def chat_completion(self, *, system: str, history: List[Dict[str, str]], model_hint=None) -> Optional[str]:
        if not self.api_key or not requests:
            return None
        model = model_hint or os.getenv("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile")
        payload = {"model": model, "max_tokens": 1024, "messages": [{"role": "system", "content": system}] + history}
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=40,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
        except Exception:
            pass
        return None


class GeminiProvider(BaseProvider):
    name = "gemini"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def chat_completion(self, *, system: str, history: List[Dict[str, str]], model_hint=None) -> Optional[str]:
        if not self.api_key or not requests:
            return None
        model = model_hint or os.getenv("GEMINI_CHAT_MODEL", "gemini-1.5-flash")
        # Gemini REST: contents from alternating user/model
        parts = []
        for m in history:
            role = m["role"]
            if role == "user":
                parts.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif role == "assistant":
                parts.append({"role": "model", "parts": [{"text": m["content"]}]})
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": parts}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        try:
            r = requests.post(url, json=body, timeout=40)
            if r.status_code == 200:
                jd = r.json()
                return jd["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            pass
        return None


def build_chain(groq_key: str, gemini_key: str) -> List[BaseProvider]:
    chain: List[BaseProvider] = []
    if groq_key:
        chain.append(GroqProvider(groq_key))
    if gemini_key:
        chain.append(GeminiProvider(gemini_key))
    return chain


def run_chat(chain: List[BaseProvider], system: str, history: List[Dict[str, str]]) -> Optional[str]:
    order = json.loads(os.getenv("AI_PROVIDER_ORDER_JSON", "[\"groq\",\"gemini\"]"))
    name_to_prov = {p.name: p for p in chain}
    seq = []
    for n in order:
        if n in name_to_prov:
            seq.append(name_to_prov[n])
    for p in chain:
        if p not in seq:
            seq.append(p)
    for p in seq:
        out = p.chat_completion(system=system, history=history)
        if out:
            return out
    return None

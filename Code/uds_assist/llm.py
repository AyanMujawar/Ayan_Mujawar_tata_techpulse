"""Minimal Ollama client returning parsed JSON. Runs fully locally (localhost:11434)."""
from __future__ import annotations

import json
import re
import time

import requests

from .config import load_config


class LLMUnavailable(RuntimeError):
    pass


def available() -> bool:
    cfg = load_config()["llm"]
    try:
        tags = requests.get(f"{cfg['base_url']}/api/tags", timeout=2).json()
        return any(m["name"].startswith(cfg["model"]) for m in tags.get("models", []))
    except Exception:
        return False


def _extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def chat_json(system: str, user: str, schema: dict | None = None) -> tuple[dict, dict]:
    """Returns (parsed_json, meta). meta has model, latency and raw text for the audit log."""
    cfg = load_config()["llm"]
    body = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "format": schema or "json",
        "think": cfg.get("think", False),
        "options": {"temperature": cfg["temperature"], "num_ctx": cfg["num_ctx"]},
    }
    t0 = time.perf_counter()
    try:
        r = requests.post(f"{cfg['base_url']}/api/chat", json=body, timeout=cfg["timeout_s"])
        r.raise_for_status()
    except Exception as exc:
        raise LLMUnavailable(str(exc)) from exc
    raw = r.json()["message"]["content"]
    meta = {"model": cfg["model"], "latency_s": round(time.perf_counter() - t0, 2), "raw": raw}
    try:
        return _extract_json(raw), {**meta, "json_valid": True}
    except Exception:
        return {}, {**meta, "json_valid": False}

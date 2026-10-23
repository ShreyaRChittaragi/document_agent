"""LLM client: OpenAI-compatible providers (Groq / Ollama) with retry + provider fallback.

LLM_CHAIN entries are tried in order and may name a model: "groq", "groq:openai/gpt-oss-20b", "ollama".
A different Groq model has its own rate-limit bucket, so it is a useful fallback for 429s.

Every call returns a parsed JSON dict. Failure handling:
- bad JSON        -> feed the error back to the model and retry
- 429 / 5xx       -> wait (honours Retry-After) and retry, then fall back to next entry
- 400/401/403/404 -> never retry, fall back immediately
- connection refused (e.g. Ollama not running) -> skip provider immediately
"""
import json
import logging
import os
import re
import time

import httpx

log = logging.getLogger("agent.llm")


class LLMError(Exception):
    pass


class TruncatedError(Exception):
    """Model hit its output limit mid-answer (finish_reason == 'length')."""


def _providers() -> list[str]:
    return [p.strip() for p in os.getenv("LLM_CHAIN", "groq,groq:openai/gpt-oss-20b,ollama").split(",") if p.strip()]


def _config(entry: str) -> dict:
    name, _, model = entry.partition(":")
    if name == "groq":
        return {
            "url": "https://api.groq.com/openai/v1/chat/completions",
            "key": os.getenv("GROQ_API_KEY", ""),
            "model": model or os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        }
    if name == "ollama":
        return {
            "url": os.getenv("OLLAMA_URL", "http://localhost:11434/v1/chat/completions"),
            "key": "",
            "model": model or os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
        }
    raise LLMError(f"unknown provider {entry}")


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)  # fall back to outermost braces
        if not m:
            raise
        return json.loads(m.group(0))


def _call(cfg: dict, messages: list[dict]) -> str:
    headers = {"Authorization": f"Bearer {cfg['key']}"} if cfg["key"] else {}
    body = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    if cfg["model"].startswith("openai/gpt-oss"):   # reasoning models: keep thinking short, leave room for the answer
        body["reasoning_effort"] = "low"
        body["max_completion_tokens"] = 4096
    else:
        body["max_tokens"] = 3000
    r = httpx.post(cfg["url"], json=body, headers=headers, timeout=90)
    r.raise_for_status()
    choice = r.json()["choices"][0]
    if choice.get("finish_reason") == "length":
        raise TruncatedError(choice["message"].get("content") or "{}")
    return choice["message"]["content"]


def _retry_wait(e: httpx.HTTPStatusError, attempt: int) -> float:
    ra = e.response.headers.get("retry-after")
    try:
        return min(float(ra), 30) + 0.5 if ra else min(5 * (attempt + 1), 20)
    except ValueError:
        return 5


def complete_json(stage: str, system: str, user: str, retries: int = 3) -> dict:
    """stage is a label used for logging and by the offline mock provider."""
    last_err: Exception | None = None
    for entry in _providers():
        if entry == "mock":
            from .mock_llm import mock_response
            return mock_response(stage, user)
        try:
            cfg = _config(entry)
        except LLMError as e:
            last_err = e
            continue
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for attempt in range(retries + 1):
            raw = ""
            try:
                raw = _call(cfg, messages)
                return _extract_json(raw)
            except json.JSONDecodeError as e:
                log.warning("%s/%s bad JSON (attempt %d)", entry, stage, attempt)
                messages += [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": "That was not valid JSON. Reply with ONLY the JSON object."},
                ]
                last_err = e
            except TruncatedError as e:
                log.warning("%s/%s output truncated (attempt %d)", entry, stage, attempt)
                messages += [
                    {"role": "assistant", "content": e.args[0]},
                    {"role": "user", "content": "Your reply was cut off. Reply again with the complete JSON, "
                                                "but shorter: max 3 short paragraphs and 4 bullets."},
                ]
                last_err = e
            except httpx.HTTPStatusError as e:
                last_err = e
                code = e.response.status_code
                if code in (400, 401, 403, 404):
                    log.error("%s/%s non-retryable %s, falling back", entry, stage, code)
                    break
                wait = _retry_wait(e, attempt) if code == 429 else 2 ** attempt
                log.warning("%s/%s HTTP %s (attempt %d), waiting %.1fs", entry, stage, code, attempt, wait)
                time.sleep(wait)
            except httpx.ConnectError as e:
                log.error("%s unreachable, skipping provider", entry)
                last_err = e
                break
            except (httpx.HTTPError, KeyError) as e:
                log.warning("%s/%s call failed (attempt %d): %s", entry, stage, attempt, e)
                last_err = e
                time.sleep(2 ** attempt)
        log.error("provider %s exhausted for stage %s, falling back", entry, stage)
    raise LLMError(f"all providers failed for stage '{stage}': {last_err}")
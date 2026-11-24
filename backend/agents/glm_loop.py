"""Z.ai GLM calls (Chat Completions) for Sparrow's agent loop.

The runtime keeps every session in one message format (user and assistant
turns with text, tool_use and tool_result blocks). This module translates that
history into Chat Completions messages for each call and translates the answer
back, so receipts, crash repair, budgets and tools work on it directly.

GLM 5.3 always thinks. Its reasoning text is kept in the assistant turn as a
``glm_reasoning`` block and returned on later calls with ``clear_thinking``
off, as Z.ai asks for multi-turn tool use. Sparrow always sends
``reasoning_effort``: the provider's default is ``max``.

Structured single-turn calls (page checks, pick reviews, picture reading) use
JSON mode: GLM has no schema mode, so the schema goes into the instructions and
the answer is validated here, with one retry that names what was wrong.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

DEFAULT_BASE = "https://api.z.ai/api/paas/v4"
# Levels GLM 5.3 and 5.3-Flash accept; the provider's own default is max.
EFFORTS = ("low", "high", "max")
# Z.ai business error codes that are not worth retrying.
NO_CREDIT = {"1113"}


GLM_PREFIXES = ("glm-",)
# GLM models that read images (Z.ai's vision request lists only the flash family).
GLM_VISION = ("glm-5.3-flash", "glm-5.3-flashx")


def is_glm_model(model: str) -> bool:
    return (model or "").lower().startswith(GLM_PREFIXES)


def reads_images(model: str) -> bool:
    """Whether Sparrow can send this model a picture (Blu-ray/DVD subtitles)."""
    return (model or "").lower() in GLM_VISION


class Block:
    """One assistant block: text, tool_use or glm_reasoning."""

    def __init__(self, type: str, **fields):
        self.type = type
        self.__dict__.update(fields)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def base_url() -> str:
    return (os.getenv("SPARROW_GLM_BASE_URL") or DEFAULT_BASE).rstrip("/")


class GLMStatusError(Exception):
    def __init__(self, status_code: int, message: str, code: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


# ─── History → Chat Completions ───────────────────────────────────────────


def chat_messages(system, messages: list[dict]) -> list[dict]:
    """The session's history as Chat Completions messages."""
    out: list[dict] = []
    system = system if isinstance(system, str) else _text(system)
    if system:
        out.append({"role": "system", "content": system})
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = "\n".join(b.get("text", "") for b in content or [] if b.get("type") == "text" and b.get("text"))
            reasoning = "".join(b.get("text", "") for b in content or [] if b.get("type") == "glm_reasoning")
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)},
                }
                for b in content or []
                if b.get("type") == "tool_use"
            ]
            entry = {"role": "assistant", "content": text}
            if reasoning:
                entry["reasoning_content"] = reasoning
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
            continue
        # A user turn: tool results answer the previous assistant turn, so they
        # come first; any text after them is a new user message.
        texts = []
        for block in content or []:
            kind = block.get("type")
            if kind == "tool_result":
                output = _text(block.get("content"))
                if block.get("is_error"):
                    output = "Error: " + output
                out.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": output})
            elif kind == "text" and block.get("text"):
                texts.append(block["text"])
        if texts:
            out.append({"role": role, "content": "\n".join(texts)})
    return out


def request_body(model, system, tools, messages, *, max_tokens, effort=""):
    body = {
        "model": model,
        "messages": chat_messages(system, messages),
        "max_tokens": max_tokens or 16000,
        "stream": False,
        "thinking": {"type": "enabled", "clear_thinking": False},
        "reasoning_effort": effort if effort in EFFORTS else "low",
    }
    if tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            }
            for tool in tools
        ]
        body["tool_choice"] = "auto"
    return body


# ─── Answer → the runtime's response ─────────────────────────────────────

STOP = {"tool_calls": "tool_use", "length": "max_tokens", "stop": "end_turn"}


def _arguments(raw):
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {"_unparsed_arguments": raw or ""}
    return value if isinstance(value, dict) else {"_unparsed_arguments": raw}


def usage_of(data: dict) -> dict:
    """Tokens as the ledger counts them: input excludes cache reads."""
    usage = data.get("usage") or {}
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    return {
        "input_tokens": max(0, (usage.get("prompt_tokens") or 0) - cached),
        "output_tokens": usage.get("completion_tokens") or 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cached,
    }


def response_object(data: dict):
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = []
    if message.get("reasoning_content"):
        content.append(Block("glm_reasoning", text=message["reasoning_content"]))
    if message.get("content"):
        content.append(Block("text", text=message["content"]))
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        content.append(Block("tool_use", id=call.get("id") or "", name=function.get("name", ""),
                             input=_arguments(function.get("arguments"))))
    finish = choice.get("finish_reason") or ""
    return SimpleNamespace(content=content, stop_reason=STOP.get(finish, finish),
                           usage=SimpleNamespace(**usage_of(data)))


# ─── Transport ────────────────────────────────────────────────────────────


def _error(response) -> GLMStatusError:
    try:
        error = response.json().get("error") or {}
    except ValueError:
        error = {}
    code, message = str(error.get("code") or ""), str(error.get("message") or "")
    if code in NO_CREDIT:
        # Not an outage: retrying cannot help until the account is topped up.
        return GLMStatusError(402, "The Z.ai account has no credit. Top it up, then try again.", code)
    return GLMStatusError(response.status_code, f"Z.ai returned HTTP {response.status_code}. {message}"[:300], code)


async def post(api_key, body, *, timeout=180) -> dict:
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            base_url() + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
    if response.status_code != 200:
        raise _error(response)
    data = response.json()
    if data.get("error"):
        raise _error(response)
    return data


async def create(api_key, model, system, tools, messages, *, max_tokens, effort="", timeout=180):
    body = request_body(model, system, tools, messages, max_tokens=max_tokens, effort=effort)
    return response_object(await post(api_key, body, timeout=timeout))


# ─── Structured single-turn calls ────────────────────────────────────────


def schema_errors(value, schema, path="answer") -> list[str]:
    """What is wrong with a JSON answer against the small schemas Sparrow
    sends (objects, arrays, enums, strings, numbers, booleans)."""
    errors = []
    kind = schema.get("type")
    if isinstance(kind, list):  # e.g. ["string", "null"]: any one will do
        tries = [schema_errors(value, {**schema, "type": k}, path) for k in kind]
        return [] if any(not t for t in tries) else tries[0]
    if kind == "null":
        return [] if value is None else [f"{path} must be null"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']}")
    if kind == "object" or (kind is None and "properties" in schema):
        if not isinstance(value, dict):
            return [f"{path} must be an object"]
        for name in schema.get("required") or []:
            if name not in value:
                errors.append(f"{path}.{name} is missing")
        properties = schema.get("properties") or {}
        for name, item in value.items():
            if name in properties:
                errors.extend(schema_errors(item, properties[name], f"{path}.{name}"))
    elif kind == "array":
        if not isinstance(value, list):
            return [f"{path} must be an array"]
        for index, item in enumerate(value[:400]):
            errors.extend(schema_errors(item, schema.get("items") or {}, f"{path}[{index}]"))
    elif kind == "string" and not isinstance(value, str):
        errors.append(f"{path} must be a string")
    elif kind == "boolean" and not isinstance(value, bool):
        errors.append(f"{path} must be true or false")
    elif kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        errors.append(f"{path} must be an integer")
    elif kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        errors.append(f"{path} must be a number")
    return errors[:20]


def _instructions(system, schema):
    return (f"{system}\n\nAnswer with one JSON object and nothing else. It must match this JSON Schema:\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":")))


def _add(total, usage):
    for key, value in usage.items():
        total[key] = total.get(key, 0) + value


async def structured(api_key, model, effort, system, user_content, schema, *, max_tokens=8000, timeout=180):
    """One JSON answer validated against schema; (data, usage). One retry
    names what was wrong; a second bad answer raises ValueError."""
    messages = [{"role": "system", "content": _instructions(system, schema)},
                {"role": "user", "content": user_content}]
    total: dict = {}
    for attempt in range(2):
        data = await post(api_key, {
            "model": model, "messages": messages, "max_tokens": max_tokens, "stream": False,
            "reasoning_effort": effort if effort in EFFORTS else "low",
            "response_format": {"type": "json_object"},
        }, timeout=timeout)
        _add(total, usage_of(data))
        choice = (data.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise ValueError("The answer stopped early (max_tokens).")
        text = (choice.get("message") or {}).get("content") or ""
        try:
            answer = json.loads(text)
            problems = schema_errors(answer, schema)
        except ValueError:
            answer, problems = None, ["the answer is not valid JSON"]
        if not problems:
            return answer, total
        if attempt == 0:
            messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": "That answer does not match the schema: " + "; ".join(problems)
                 + ". Answer again with the whole corrected JSON object."},
            ]
    raise ValueError("The model's answer did not match the schema: " + "; ".join(problems)[:200])


def caller(api_key, model, effort="low"):
    """Structured single-turn calls, shaped like subtitle_contract's callers."""

    async def call(system, prompt, schema):
        return await structured(api_key, model, effort, system, prompt, schema)

    call.model = model
    return call


def reader(api_key, model, system, schema, effort="low"):
    """A vision call: one sheet image in, its rows' text out (GLM 5.3 Flash)."""

    async def read(image_b64, numbers):
        content = [
            {"type": "text", "text": f"Transcribe rows {numbers[0]}–{numbers[-1]}."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + image_b64}},
        ]
        return await structured(api_key, model, effort, system, content, schema)

    read.model = model
    return read

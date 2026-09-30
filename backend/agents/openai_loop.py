"""OpenAI Responses API calls for Sparrow's agent loop.

The runtime keeps every session in one message format (user and assistant
turns with text, tool_use and tool_result blocks). This module translates that
history into Responses API input items for each call and translates the answer
back, so receipts, crash repair, budgets and tools work unchanged whichever
provider a session's model belongs to.

Reasoning items are stored in the assistant turn as ``openai_reasoning``
blocks (encrypted, with ``store`` off) and passed back on later calls, so a
reasoning model keeps its chain of thought across tool calls.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

OPENAI_PREFIXES = ("gpt-", "o1", "o3", "o4")


def is_openai_model(model: str) -> bool:
    return (model or "").lower().startswith(OPENAI_PREFIXES)


class OpenAIStatusError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class Block:
    """One assistant block, shaped like the Anthropic SDK's content blocks."""

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


def input_items(messages: list[dict]) -> list[dict]:
    """The session's history as Responses API input items."""
    items: list[dict] = []
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if isinstance(content, str):
            items.append({"role": role, "content": content})
            continue
        for block in content or []:
            kind = block.get("type")
            if kind == "text" and block.get("text"):
                items.append({"role": role, "content": block["text"]})
            elif kind == "tool_use":
                items.append(
                    {
                        "type": "function_call",
                        "call_id": block["id"],
                        "name": block["name"],
                        "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                    }
                )
            elif kind == "tool_result":
                output = _text(block.get("content"))
                if block.get("is_error"):
                    output = "Error: " + output
                items.append({"type": "function_call_output", "call_id": block["tool_use_id"], "output": output})
            elif kind == "openai_reasoning":
                items.append(block["item"])
    return items


def request_body(model, system, tools, messages, *, max_tokens, effort="", cache_key=""):
    body = {
        "model": model,
        "instructions": system if isinstance(system, str) else _text(system),
        "input": input_items(messages),
        "tools": [
            {
                "type": "function",
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
                "strict": False,
            }
            for tool in tools
        ],
        "parallel_tool_calls": True,
        "max_output_tokens": max_tokens,
        "store": False,
        "include": ["reasoning.encrypted_content"],
    }
    if effort:
        body["reasoning"] = {"effort": effort}
    if cache_key:
        body["prompt_cache_key"] = cache_key
    return body


def response_object(data: dict):
    """A Responses API answer as the runtime's response: content blocks and usage."""
    content = []
    for item in data.get("output", []):
        kind = item.get("type")
        if kind == "reasoning":
            content.append(Block("openai_reasoning", item=item))
        elif kind == "message":
            text = "".join(p.get("text", "") for p in item.get("content", []) if p.get("type") == "output_text")
            if text:
                content.append(Block("text", text=text))
        elif kind == "function_call":
            try:
                arguments = json.loads(item.get("arguments") or "{}")
            except ValueError:
                arguments = {"_unparsed_arguments": item.get("arguments", "")}
            if not isinstance(arguments, dict):
                arguments = {"_unparsed_arguments": item.get("arguments", "")}
            content.append(Block("tool_use", id=item["call_id"], name=item["name"], input=arguments))
    usage = data.get("usage") or {}
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
    return SimpleNamespace(
        content=content,
        stop_reason=data.get("status"),
        usage=SimpleNamespace(
            input_tokens=max(0, (usage.get("input_tokens") or 0) - cached),
            output_tokens=usage.get("output_tokens") or 0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=cached,
        ),
    )


async def create(api_key, model, system, tools, messages, *, max_tokens, effort="", cache_key="", timeout=180):
    import httpx

    body = request_body(model, system, tools, messages, max_tokens=max_tokens, effort=effort, cache_key=cache_key)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
    if response.status_code != 200:
        try:
            message = (response.json().get("error") or {}).get("message", "")
        except ValueError:
            message = ""
        raise OpenAIStatusError(response.status_code, f"OpenAI returned HTTP {response.status_code}. {message}"[:300])
    return response_object(response.json())

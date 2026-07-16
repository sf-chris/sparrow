"""
The agent runtime: an LLM in a tool-use loop. Nothing else qualifies.

A session wakes on an event (or its own timer), reasons across as many
tool calls as it needs, then either hibernates with a trigger or closes.
The full message history persists, so a session that wakes three days
later still remembers everything it tried.

Intelligence lives here, in the loop. Everything in tools.py is just the
tool belt.
"""
from __future__ import annotations
import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import anthropic

from .models import AgentSession, Event, SessionStatus
from .store import AgentStore

logger = logging.getLogger("sparrow.agents")

# USD per million tokens. Each call stores the rates applied so historical
# ledger entries remain reproducible after provider prices change.
SONNET_5_LAUNCH_END = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
PRICING_SOURCE = "Anthropic public Claude API list pricing"


def rates_for_model(model: str, at: Optional[float] = None) -> dict[str, float]:
    at = at or time.time()
    normalized = (model or "").lower()
    if "sonnet-5" in normalized:
        base_input, output = ((2.0, 10.0) if at < SONNET_5_LAUNCH_END else (3.0, 15.0))
    elif any(name in normalized for name in ("opus-4-8", "opus-4-7", "opus-4-6", "opus-4-5")):
        base_input, output = 5.0, 25.0
    elif "opus" in normalized:
        base_input, output = 15.0, 75.0
    elif "haiku-4-5" in normalized:
        base_input, output = 1.0, 5.0
    elif "haiku-3-5" in normalized:
        base_input, output = 0.8, 4.0
    elif "haiku-3" in normalized:
        base_input, output = 0.25, 1.25
    else:
        base_input, output = 3.0, 15.0
    return {
        "input": base_input,
        "output": output,
        "cache_write": base_input * 1.25,
        "cache_read": base_input * 0.1,
    }


def calculate_usage_cost(model: str, input_tokens: int, output_tokens: int,
                         cache_creation_input_tokens: int = 0,
                         cache_read_input_tokens: int = 0,
                         at: Optional[float] = None) -> tuple[float, dict[str, float]]:
    rates = rates_for_model(model, at)
    cost = (
        input_tokens * rates["input"]
        + output_tokens * rates["output"]
        + cache_creation_input_tokens * rates["cache_write"]
        + cache_read_input_tokens * rates["cache_read"]
    ) / 1_000_000
    return cost, rates


def spend_snapshot(session: AgentSession) -> dict:
    """Serializable spend with a synthesized ledger row for older sessions."""
    spend = session.spend.to_dict()
    entries = list(spend.get("entries") or [])
    if not entries and (spend["input_tokens"] or spend["output_tokens"]):
        cost, rates = calculate_usage_cost(
            session.model,
            spend["input_tokens"], spend["output_tokens"],
            spend.get("cache_creation_input_tokens", 0),
            spend.get("cache_read_input_tokens", 0),
            session.updated_at,
        )
        entries = [{
            "ts": session.updated_at,
            "model": session.model,
            "input_tokens": spend["input_tokens"],
            "output_tokens": spend["output_tokens"],
            "cache_creation_input_tokens": spend.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": spend.get("cache_read_input_tokens", 0),
            "rates": rates,
            "cost": cost,
            "legacy_aggregate": True,
        }]
        spend["dollars"] = cost
    else:
        spend["dollars"] = sum(float(entry.get("cost") or 0) for entry in entries)
    spend["entries"] = entries
    spend["pricing_source"] = PRICING_SOURCE
    return spend

MAX_STEPS_PER_WAKE = 40       # safety net, not a leash
MAX_TOKENS = 4096
HISTORY_TRIM_AT = 120         # messages; trim down to…
HISTORY_TRIM_TO = 80


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict
    handler: Callable[["ToolCtx", dict], Awaitable[Any]]

    def to_api(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema}


@dataclass
class ToolCtx:
    """Handed to every tool handler. Control tools flip the flags."""
    session: AgentSession
    runtime: "AgentRuntime"
    extra: dict = field(default_factory=dict)
    # control flags set by tools
    hibernate: bool = False
    close: bool = False
    close_reason: str = ""


@dataclass
class AgentSpec:
    """How to run one kind of agent: its brain (system prompt), its tool
    belt, and its model tier."""
    kind: str
    model: Callable[[], str]                      # default model for new sessions
    system: Callable[[AgentSession], Awaitable[str]]
    tools: Callable[[AgentSession], list[ToolDef]]


class AgentRuntime:
    def __init__(self, store: AgentStore, api_key_getter: Callable[[], str],
                 on_session_change: Optional[Callable[[AgentSession], Awaitable[None]]] = None,
                 on_tool_activity: Optional[
                     Callable[[AgentSession, str, str, dict, str, bool], Awaitable[None]]
                 ] = None):
        self.store = store
        self._api_key_getter = api_key_getter
        self._specs: dict[str, AgentSpec] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._pending: dict[str, deque[Event]] = {}
        self._on_session_change = on_session_change
        self._on_tool_activity = on_tool_activity

    def register(self, spec: AgentSpec) -> None:
        self._specs[spec.kind] = spec

    def _client(self) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(api_key=self._api_key_getter())

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def _notify(self, session: AgentSession) -> None:
        if self._on_session_change:
            try:
                await self._on_session_change(session)
            except Exception:
                logger.exception("session change notification failed")

    # ─── Waking ──────────────────────────────────────────────────────────

    async def wake(self, session_id: str, event: Event) -> None:
        """Deliver an event to a session. If the session is mid-turn the
        event queues and is handed to the agent before it hibernates."""
        self._pending.setdefault(session_id, deque()).append(event)
        lock = self._lock(session_id)
        if lock.locked():
            return  # the running turn will drain the queue before sleeping
        async with lock:
            await self._run(session_id)

    async def _run(self, session_id: str) -> None:
        session = self.store.get_session(session_id)
        if not session or session.status == SessionStatus.CLOSED:
            self._pending.pop(session_id, None)
            return
        spec = self._specs.get(session.agent.value)
        if not spec:
            logger.error("no spec registered for agent kind %s", session.agent.value)
            return

        self._repair_interrupted(session)
        pending = self._pending.setdefault(session_id, deque())

        events = self._drain(pending)
        if not events:
            return
        session.status = SessionStatus.RUNNING
        session.wake_at = 0.0
        self.store.save_session(session)
        await self._notify(session)

        try:
            await self._turn(session, spec, events)
        except Exception:
            # A broken turn must never orphan a session: journal-grade
            # legibility happens in the agents; here we just make sure it
            # wakes again soon to retry.
            logger.exception("agent turn crashed (session %s)", session.id)
            session.status = SessionStatus.HIBERNATING
            session.wake_at = time.time() + 300
            session.wake_reason = "Something went wrong on my side — retrying shortly."
            self.store.save_session(session)
        await self._notify(session)

    def _drain(self, pending: deque[Event]) -> list[Event]:
        events = []
        while pending:
            events.append(pending.popleft())
        return events

    def _repair_interrupted(self, session: AgentSession) -> None:
        """If the process died mid-turn, the last message may be an assistant
        tool_use with no result. Patch it so the API accepts the history."""
        if not session.messages:
            return
        last = session.messages[-1]
        if last.get("role") != "assistant":
            return
        tool_uses = [b for b in last.get("content", [])
                     if isinstance(b, dict) and b.get("type") == "tool_use"]
        if tool_uses:
            session.messages.append({
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": b["id"],
                     "content": "(interrupted — Sparrow restarted before this tool ran)"}
                    for b in tool_uses
                ],
            })

    # ─── The loop ────────────────────────────────────────────────────────

    async def _turn(self, session: AgentSession, spec: AgentSpec,
                    events: list[Event]) -> None:
        ctx = ToolCtx(session=session, runtime=self)
        tools = spec.tools(session)
        tool_map = {t.name: t for t in tools}
        pending = self._pending.setdefault(session.id, deque())

        _append_user(session, self._wake_text(session, events))
        steps = 0

        while True:
            self._trim_history(session)
            system = await spec.system(session)
            response = await self._call_api(session, system, tools)
            if response is None:
                # API unreachable after retries — hibernate and try later.
                session.status = SessionStatus.HIBERNATING
                session.wake_at = time.time() + 600
                session.wake_reason = "Can't reach my reasoning service — retrying soon."
                self.store.save_session(session)
                return

            session.messages.append({
                "role": "assistant",
                "content": [b.to_dict() for b in response.content],
            })
            self._track_spend(session, response)
            steps += 1

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                break  # plain text turn end → hibernate

            results = []
            for tu in tool_uses:
                results.append(await self._run_tool(ctx, tool_map, tu))
            session.messages.append({"role": "user", "content": results})
            self.store.save_session(session)

            if ctx.close:
                session.status = SessionStatus.CLOSED
                session.closed_at = time.time()
                session.close_reason = ctx.close_reason
                session.wake_at = 0.0
                session.wake_reason = ""
                self.store.save_session(session)
                self._pending.pop(session.id, None)
                return
            if ctx.hibernate:
                # New events that arrived mid-turn beat hibernation.
                fresh = self._drain(pending)
                if fresh:
                    ctx.hibernate = False
                    _append_user(session, self._wake_text(session, fresh))
                    continue
                break
            if steps >= MAX_STEPS_PER_WAKE:
                _append_user(session,
                    "(step cap for this wake reached — write a journal note about where "
                    "you are, then hibernate with wake_me. Your work resumes on the next wake.)")
                ctx.hibernate = False
                steps = 0  # allow the wrap-up round

        session.status = SessionStatus.HIBERNATING
        session.spend.turns += 1
        if session.wake_at == 0.0 and not session.wake_reason:
            session.wake_reason = "Waiting for the next event."
        self.store.save_session(session)

        # Events that arrived at the very end: run again.
        if pending:
            await self._run(session.id)

    async def _run_tool(self, ctx: ToolCtx, tool_map: dict[str, ToolDef], tu) -> dict:
        args = tu.input or {}
        await self._notify_tool(ctx.session, tu.name, "started", args, "", False)
        tool = tool_map.get(tu.name)
        if not tool:
            content, is_error = f"Unknown tool: {tu.name}", True
        else:
            try:
                out = await tool.handler(ctx, args)
                content = out if isinstance(out, str) else _to_text(out)
                is_error = False
            except ToolError as e:
                content, is_error = str(e), True
            except Exception as e:
                logger.exception("tool %s failed", tu.name)
                content, is_error = f"Tool failed: {e}", True
        content = content[:40000]  # keep single results bounded
        await self._notify_tool(
            ctx.session, tu.name, "failed" if is_error else "completed",
            args, content, is_error,
        )
        result: dict = {"type": "tool_result", "tool_use_id": tu.id, "content": content}
        if is_error:
            result["is_error"] = True
        return result

    async def _notify_tool(self, session: AgentSession, tool_name: str, phase: str,
                           args: dict, content: str, is_error: bool) -> None:
        """Publish diagnostics without ever making logging part of agent control flow."""
        if not self._on_tool_activity:
            return
        try:
            await self._on_tool_activity(
                session, tool_name, phase, args, content, is_error,
            )
        except Exception:
            logger.exception("tool activity notification failed")

    async def _call_api(self, session: AgentSession, system: str, tools: list[ToolDef]):
        client = self._client()
        delay = 2.0
        for attempt in range(4):
            try:
                return await client.messages.create(
                    model=session.model,
                    max_tokens=MAX_TOKENS,
                    system=system,
                    tools=[t.to_api() for t in tools],
                    messages=session.messages,
                )
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                status = getattr(e, "status_code", None)
                if status and 400 <= status < 500 and status != 429:
                    raise  # our bug — don't retry blindly
                logger.warning("API error (attempt %d): %s", attempt + 1, e)
                await asyncio.sleep(delay)
                delay *= 3
        return None

    # ─── Bookkeeping ─────────────────────────────────────────────────────

    def _wake_text(self, session: AgentSession, events: list[Event]) -> str:
        lines = [f"[wake] {time.strftime('%A %Y-%m-%d %H:%M %Z', time.localtime())}"]
        for e in events:
            desc = e.payload.get("description") or e.kind
            lines.append(f"- event: {e.kind} — {desc}" if desc != e.kind else f"- event: {e.kind}")
            extras = {k: v for k, v in e.payload.items() if k != "description"}
            if extras:
                lines.append(f"  {_to_text(extras)}")
        s = session.spend
        cost = spend_snapshot(session)["dollars"]
        lines.append(
            f"[spend so far] turns: {s.turns}, searches: {s.searches}, peeks: {s.peeks}, "
            f"cost: ${cost:.4f}. Be relentless about the goal, frugal about the means — "
            f"if you're not making progress, write down why and hibernate with a trigger."
        )
        return "\n".join(lines)

    def _track_spend(self, session: AgentSession, response) -> None:
        usage = getattr(response, "usage", None)
        if not usage:
            return
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        s = session.spend
        if not s.entries and (s.input_tokens or s.output_tokens):
            legacy_cost, legacy_rates = calculate_usage_cost(
                session.model, s.input_tokens, s.output_tokens,
                s.cache_creation_input_tokens, s.cache_read_input_tokens,
                session.updated_at,
            )
            s.entries.append({
                "ts": session.updated_at, "model": session.model,
                "input_tokens": s.input_tokens, "output_tokens": s.output_tokens,
                "cache_creation_input_tokens": s.cache_creation_input_tokens,
                "cache_read_input_tokens": s.cache_read_input_tokens,
                "rates": legacy_rates, "cost": legacy_cost,
                "legacy_aggregate": True,
            })
            s.dollars = legacy_cost
        s.input_tokens += inp
        s.output_tokens += out
        s.cache_creation_input_tokens += cache_write
        s.cache_read_input_tokens += cache_read
        call_cost, rates = calculate_usage_cost(
            session.model, inp, out, cache_write, cache_read, time.time())
        s.entries.append({
            "ts": time.time(), "model": session.model,
            "input_tokens": inp, "output_tokens": out,
            "cache_creation_input_tokens": cache_write,
            "cache_read_input_tokens": cache_read,
            "rates": rates, "cost": call_cost,
        })
        s.dollars = sum(float(entry.get("cost") or 0) for entry in s.entries)

    def _trim_history(self, session: AgentSession) -> None:
        msgs = session.messages
        if len(msgs) <= HISTORY_TRIM_AT:
            return
        # Cut at a clean user-text boundary so tool_use/tool_result pairs
        # never split. A [wake] message is always such a boundary.
        cut = len(msgs) - HISTORY_TRIM_TO
        while cut < len(msgs) - 1:
            m = msgs[cut]
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                break
            cut += 1
        session.messages = [{
            "role": "user",
            "content": "(earlier context was trimmed to stay within limits — your journal "
                       "and memory notes are the durable record; re-read them if unsure)",
        }, {"role": "assistant", "content": "Understood."}] + msgs[cut:]


def _append_user(session: AgentSession, text: str) -> None:
    """Append user text, merging into a trailing user message (the API
    requires alternating roles — e.g. after a crash-repair tool_result)."""
    msgs = session.messages
    if msgs and msgs[-1].get("role") == "user":
        content = msgs[-1]["content"]
        if isinstance(content, str):
            msgs[-1]["content"] = content + "\n\n" + text
        else:
            content.append({"type": "text", "text": text})
        return
    msgs.append({"role": "user", "content": text})


class ToolError(Exception):
    """Raise from a tool handler for a clean is_error result the agent can
    reason about (vs. an unexpected crash)."""


def _to_text(obj: Any) -> str:
    import json
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)

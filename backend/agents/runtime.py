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
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import anthropic

from .models import AgentSession, Event, SessionStatus, JobStatus, CaseState
from .store import AgentStore

logger = logging.getLogger("sparrow.agents")

# USD per million tokens. Each call stores the rates applied so historical
# ledger entries remain reproducible after provider prices change.
PRICING_SOURCE = (
    "https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-09-10)"
)


def rates_for_model(model: str, at: Optional[float] = None) -> dict[str, float]:
    normalized = (model or "").lower()
    cache_read_multiplier = 0.1
    if "sonnet-5" in normalized:
        # The announced September price increase was withdrawn by the provider.
        base_input, output = 2.0, 10.0
    elif "fable-5" in normalized or "mythos-5" in normalized:
        base_input, output = 10.0, 50.0
        if "5-1" in normalized:
            cache_read_multiplier = 0.025
    elif any(
        name in normalized
        for name in ("opus-5", "opus-4-8", "opus-4-7", "opus-4-6", "opus-4-5")
    ):
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
        "cache_read": base_input * cache_read_multiplier,
    }


def calculate_usage_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    at: Optional[float] = None,
) -> tuple[float, dict[str, float]]:
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
            spend["input_tokens"],
            spend["output_tokens"],
            spend.get("cache_creation_input_tokens", 0),
            spend.get("cache_read_input_tokens", 0),
            session.updated_at,
        )
        entries = [
            {
                "ts": session.updated_at,
                "model": session.model,
                "input_tokens": spend["input_tokens"],
                "output_tokens": spend["output_tokens"],
                "cache_creation_input_tokens": spend.get(
                    "cache_creation_input_tokens", 0
                ),
                "cache_read_input_tokens": spend.get("cache_read_input_tokens", 0),
                "rates": rates,
                "cost": cost,
                "legacy_aggregate": True,
            }
        ]
        spend["dollars"] = cost
    else:
        spend["dollars"] = sum(float(entry.get("cost") or 0) for entry in entries)
    spend["entries"] = entries
    spend["pricing_source"] = PRICING_SOURCE
    return spend


MAX_STEPS_PER_WAKE = 40  # safety net, not a leash
MAX_TOKENS = 4096
HISTORY_TRIM_AT = 120  # messages; trim down to…
HISTORY_TRIM_TO = 80


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict
    handler: Callable[["ToolCtx", dict], Awaitable[Any]]

    def to_api(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class ToolCtx:
    """Handed to every tool handler. Control tools flip the flags."""

    session: AgentSession
    runtime: "AgentRuntime"
    extra: dict = field(default_factory=dict)
    job_revision: int = field(init=False)

    def __post_init__(self):
        self.job_revision = self.session.job_revision

    # control flags set by tools
    hibernate: bool = False
    close: bool = False
    close_reason: str = ""


@dataclass
class AgentSpec:
    """How to run one kind of agent: its brain (system prompt), its tool
    belt, and its model tier."""

    kind: str
    model: Callable[[], str]  # default model for new sessions
    system: Callable[[AgentSession], Awaitable[str]]
    tools: Callable[[AgentSession], list[ToolDef]]
    max_steps: int = MAX_STEPS_PER_WAKE
    max_tokens: int = MAX_TOKENS
    # Cache the growing conversation prefix: long single-session loops re-send
    # their history on every call, so cached reads cut input cost about tenfold.
    cache: bool = False
    effort: str = ""  # output_config effort; empty uses the model's default


class AgentRuntime:
    def __init__(
        self,
        store: AgentStore,
        api_key_getter: Callable[[], str],
        on_session_change: Optional[Callable[[AgentSession], Awaitable[None]]] = None,
        on_tool_activity: Optional[
            Callable[[AgentSession, str, str, dict, str, bool], Awaitable[None]]
        ] = None,
    ):
        self.store = store
        self._api_key_getter = api_key_getter
        self._specs: dict[str, AgentSpec] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._on_session_change = on_session_change
        self._on_tool_activity = on_tool_activity
        self.policy_getter = lambda: {
            "max_agent_calls": MAX_STEPS_PER_WAKE,
            "max_agent_dollars": 3,
        }
        self._budget_locks: dict[str, asyncio.Lock] = {}
        with self.store._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS reasoning_reservations(id TEXT PRIMARY KEY,scope TEXT NOT NULL,dollars REAL NOT NULL,created REAL NOT NULL)"
            )

    def authority_valid(
        self, session: AgentSession, revision: int, completing: bool = False
    ) -> bool:
        current = self.store.get_session(session.id)
        if current and current.status == SessionStatus.CLOSED:
            return False
        if session.user_id:
            from .accounts import Accounts

            user = Accounts(self.store.data_dir).user(session.user_id)
            if not user or user["disabled"]:
                return False
        if not session.job_id:
            return True
        job = self.store.get_job(session.job_id)
        allowed = {JobStatus.ACTIVE}
        if completing:
            allowed.update((JobStatus.COMPLETE, JobStatus.ABANDONED))
        return bool(job and job.status in allowed and job.revision == revision)

    def register(self, spec: AgentSpec) -> None:
        self._specs[spec.kind] = spec

    def tools_for(self, session: AgentSession) -> list[ToolDef]:
        from .evidence import PAGE_CHARS

        async def read(ctx, args):
            try:
                return self.store.evidence.read(
                    ctx.session, args.get("evidence_id"), args.get("offset", 0),
                    args.get("limit", PAGE_CHARS),
                )
            except ValueError as exc:
                raise ToolError(str(exc)) from exc

        async def listing(ctx, args):
            try:
                return self.store.evidence.list(ctx.session, args.get("after", 0))
            except ValueError as exc:
                raise ToolError(str(exc)) from exc

        tools = self._specs[session.agent.value].tools(session)
        if any(tool.name in {"evidence_read", "evidence_list"} for tool in tools):
            raise ValueError("Evidence retrieval tool names are reserved by the runtime.")
        return [*tools, ToolDef(
            "evidence_read", "Read a page of a complete saved tool observation. "
            "Use this when a result preview is partial; never infer missing items from a preview.",
            {"type": "object", "properties": {
                "evidence_id": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": PAGE_CHARS},
            }, "required": ["evidence_id"], "additionalProperties": False}, read,
        ), ToolDef(
            "evidence_list", "Find saved oversized observations from this session, "
            "including references lost from older conversation context. No provider calls.",
            {"type": "object", "properties": {
                "after": {"type": "integer", "minimum": 0},
            }, "additionalProperties": False}, listing,
        )]

    def _client(self) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(
            api_key=self._api_key_getter(), timeout=45, max_retries=0
        )

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
        self.store.enqueue_delivery(session_id, event)
        lock = self._lock(session_id)
        if lock.locked():
            return  # the running turn will drain the queue before sleeping
        async with lock:
            await self._run(session_id)

    async def _run(self, session_id: str) -> None:
        session = self.store.get_session(session_id)
        if not session or session.status == SessionStatus.CLOSED:
            if session:
                self.store.acknowledge_events(
                    session, self.store.pending_events(session_id)
                )
            return
        spec = self._specs.get(session.agent.value)
        if not spec:
            logger.error("no spec registered for agent kind %s", session.agent.value)
            return

        self._repair_interrupted(session)
        events = self.store.pending_events(session_id)
        if not events:
            return
        if session.status == SessionStatus.CLOSED:
            self.store.acknowledge_events(session, events)
            return
        if not self.authority_valid(session, session.job_revision):
            return
        session.status = SessionStatus.RUNNING
        session.outcome = CaseState.ACTIVE
        session.wake_at = 0.0
        session.wake_reason = ""
        self.store.save_session(session)
        await self._notify(session)

        try:
            await self._turn(session, spec, events)
        except Exception:
            # A broken turn must never orphan a session: journal-grade
            # legibility happens in the agents; here we just make sure it
            # wakes again soon to retry.
            logger.exception("agent turn crashed (session %s)", session.id)
            if not self.authority_valid(session, session.job_revision):
                return
            session.status = SessionStatus.HIBERNATING
            session.outcome = CaseState.FAILED
            session.wake_at = time.time() + 300
            session.wake_reason = "Something went wrong. Trying again in 5 minutes."
            self.store.save_session(session)
        await self._notify(session)

    def _repair_interrupted(self, session: AgentSession) -> None:
        """Recover recorded results; never describe an uncertain effect as unrun."""
        if not session.messages:
            return
        last = session.messages[-1]
        if last.get("role") != "assistant" or not isinstance(last.get("content"), list):
            return
        results = []
        for block in last["content"]:
            if block.get("type") != "tool_use":
                continue
            receipt = self.store.invocation(session.id, block["id"])
            if receipt and receipt["result"] is not None:
                results.append(receipt["result"])
                if receipt["control"].get("close"):
                    session.status = SessionStatus.CLOSED
                    session.outcome = self._completion_outcome(session)
                    session.closed_at = time.time()
                    session.close_reason = receipt["control"].get("close_reason", "")
                    session.wake_at = 0
                    session.wake_reason = ""
            else:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "is_error": True,
                        "content": "Tool interrupted by a restart. Its effect may already have happened. "
                        "Inspect current inventory, requests and durable node receipts before retrying; "
                        "do not assume the action failed or repeat it blindly.",
                    }
                )
        if results:
            session.messages.append({"role": "user", "content": results})
            self.store.save_session(session)

    def _completion_outcome(self, session):
        job = self.store.get_job(session.job_id) if session.job_id else None
        return (
            CaseState.FAILED
            if job and job.status == JobStatus.ABANDONED
            else CaseState.COMPLETED
        )

    def _budget_sessions(self, session):
        if session.budget_scope:
            return [
                s
                for s in self.store.get_sessions()
                if s.budget_scope == session.budget_scope
                or (
                    session.agent.value == "librarian"
                    and s.agent == session.agent
                    and s.user_id == session.user_id
                    and s.download_id == session.download_id
                )
            ]
        return (
            self.store.get_sessions(job_id=session.job_id)
            if session.job_id
            else [session]
        )

    # ─── The loop ────────────────────────────────────────────────────────

    async def _turn(
        self, session: AgentSession, spec: AgentSpec, events: list[Event]
    ) -> None:
        ctx = ToolCtx(session=session, runtime=self)
        tools = self.tools_for(session)
        tool_map = {t.name: t for t in tools}

        _append_user(session, self._wake_text(session, events))
        self.store.acknowledge_events(session, events)
        steps = 0

        while True:
            if not self.authority_valid(session, ctx.job_revision):
                return
            policy = self.policy_getter()
            sessions = self._budget_sessions(session)
            dollars = (
                sum(s.spend.dollars for s in sessions if s.id != session.id)
                + session.spend.dollars
            )
            if (
                steps
                >= min(MAX_STEPS_PER_WAKE, spec.max_steps, policy["max_agent_calls"])
                or dollars >= policy["max_agent_dollars"]
            ):
                session.outcome = (
                    CaseState.BUDGET_LIMITED
                    if dollars >= policy["max_agent_dollars"]
                    else CaseState.NEEDS_INPUT
                )
                session.wake_reason = (
                    "Reached the spending limit. Raise it in Defaults, then try again."
                    if dollars >= policy["max_agent_dollars"]
                    else "Reached the agent step limit. Try again to continue."
                )
                session.wake_at = 0
                break
            self._trim_history(session)
            system = await spec.system(session)
            response, limited = await self._budgeted_call(session, system, tools, spec)
            if limited:
                session.outcome = CaseState.BUDGET_LIMITED
                session.wake_at = 0
                session.wake_reason = "Stopped before going over the spending limit. Raise it in Defaults, then try again."
                break
            if not self.authority_valid(session, ctx.job_revision):
                return
            if response is None:
                # API unreachable after retries — hibernate and try later.
                session.status = SessionStatus.HIBERNATING
                session.outcome = CaseState.WAITING
                session.wake_at = time.time() + 600
                session.wake_reason = (
                    "Can't reach Anthropic. Trying again in 10 minutes."
                )
                self.store.save_session(session)
                return

            session.messages.append(
                {
                    "role": "assistant",
                    "content": [b.to_dict() for b in response.content],
                }
            )
            self.store.save_session(
                session
            )  # record every invocation before any effect
            steps += 1

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                session.outcome = CaseState.NEEDS_INPUT
                session.wake_reason = (
                    "Stopped without a result. Try again, or change the request."
                )
                break

            results = []
            for tu in tool_uses:
                results.append(await self._run_tool(ctx, tool_map, tu))
            session.messages.append({"role": "user", "content": results})
            if not self.authority_valid(
                session, ctx.job_revision, completing=ctx.close
            ):
                return
            self.store.save_session(session)

            if ctx.close:
                session.status = SessionStatus.CLOSED
                session.outcome = self._completion_outcome(session)
                session.closed_at = time.time()
                session.close_reason = ctx.close_reason
                session.wake_at = 0.0
                session.wake_reason = ""
                self.store.save_session(session)
                self.store.acknowledge_events(
                    session, self.store.pending_events(session.id)
                )
                return
            if ctx.hibernate:
                # New events that arrived mid-turn beat hibernation.
                fresh = self.store.pending_events(session.id)
                if fresh:
                    ctx.hibernate = False
                    _append_user(session, self._wake_text(session, fresh))
                    self.store.acknowledge_events(session, fresh)
                    continue
                break

        session.status = SessionStatus.HIBERNATING
        if session.outcome == CaseState.ACTIVE:
            session.outcome = CaseState.WAITING
        session.spend.turns += 1
        if session.wake_at == 0.0 and not session.wake_reason:
            session.wake_reason = "Waiting for an update."
        self.store.save_session(session)

        # Events that arrived at the very end: run again.
        if self.store.pending_events(session.id):
            await self._run(session.id)

    async def _run_tool(self, ctx: ToolCtx, tool_map: dict[str, ToolDef], tu) -> dict:
        args = tu.input or {}
        if ctx.close:
            return {
                "type": "tool_result",
                "tool_use_id": tu.id,
                "is_error": True,
                "content": "The session already finished. Further actions in this batch are refused.",
            }
        receipt = self.store.invocation(ctx.session.id, tu.id)
        if receipt:
            if (
                receipt["name"] != tu.name
                or receipt["arguments"] != args
                or receipt["revision"] != ctx.job_revision
            ):
                return {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "is_error": True,
                    "content": "Tool invocation identity was reused with changed inputs. Inspect current evidence.",
                }
            if receipt["result"] is not None:
                for key, value in receipt["control"].items():
                    setattr(ctx, key, value)
                return receipt["result"]
            return {
                "type": "tool_result",
                "tool_use_id": tu.id,
                "is_error": True,
                "content": "Previous invocation has an uncertain outcome. Reconcile current receipts before retrying.",
            }
        self.store.start_invocation(ctx.session, tu.id, tu.name, args)
        await self._notify_tool(ctx.session, tu.name, "started", args, "", False)
        tool = tool_map.get(tu.name)
        if not tool:
            content, is_error = f"Unknown tool: {tu.name}", True
        else:
            try:
                if not self.authority_valid(ctx.session, ctx.job_revision):
                    raise ToolError(
                        "Request authority changed; this action was stopped."
                    )
                out = await tool.handler(ctx, args)
                content = out if isinstance(out, str) else _to_text(out)
                is_error = False
            except ToolError as e:
                content, is_error = str(e), True
            except Exception as e:
                logger.exception("tool %s failed", tu.name)
                content, is_error = f"Tool failed: {e}", True
        result: dict = {"type": "tool_result", "tool_use_id": tu.id, "content": content}
        if is_error:
            result["is_error"] = True
        current = self.store.get_session(ctx.session.id)
        if current and (
            current.status == SessionStatus.CLOSED
            or current.job_revision != ctx.job_revision
            or not self.authority_valid(
                ctx.session, ctx.job_revision, completing=ctx.close
            )
        ):
            ctx.close = ctx.hibernate = False
            snapshot = current
        else:
            snapshot = ctx.session
        result = self.store.finish_invocation(
            snapshot,
            tu.id,
            result,
            {
                "close": ctx.close,
                "close_reason": ctx.close_reason,
                "hibernate": ctx.hibernate,
            },
        )
        content = result["content"]
        is_error = bool(result.get("is_error"))
        await self._notify_tool(
            ctx.session,
            tu.name,
            "failed" if is_error else "completed",
            args,
            content,
            is_error,
        )
        return result

    async def _notify_tool(
        self,
        session: AgentSession,
        tool_name: str,
        phase: str,
        args: dict,
        content: str,
        is_error: bool,
    ) -> None:
        """Publish diagnostics without ever making logging part of agent control flow."""
        if not self._on_tool_activity:
            return
        try:
            await self._on_tool_activity(
                session,
                tool_name,
                phase,
                args,
                content,
                is_error,
            )
        except Exception:
            logger.exception("tool activity notification failed")

    async def _budgeted_call(self, session, system, tools, spec):
        scope = session.budget_scope or session.job_id or session.id
        async with self._budget_locks.setdefault(scope, asyncio.Lock()):
            if not self.authority_valid(session, session.job_revision):
                return None, False
            sessions = self._budget_sessions(session)
            spent = (
                sum(s.spend.dollars for s in sessions if s.id != session.id)
                + session.spend.dollars
            )
            # UTF-8 bytes plus framing provide a conservative token allowance.
            # Reserve for all three possible attempts. An interrupted or
            # ambiguous call retains its reservation across server restarts.
            wire = json.dumps(
                {
                    "system": system,
                    "messages": session.messages,
                    "tools": [t.to_api() for t in tools],
                },
                ensure_ascii=False,
            )
            tokens = len(wire.encode("utf8")) + 1024 * (
                len(session.messages) + len(tools) + 1
            )
            rates = rates_for_model(session.model)
            estimate = (
                3
                * (tokens * rates["cache_write"] + spec.max_tokens * rates["output"])
                / 1_000_000
            )
            identity = secrets.token_hex(16)
            with self.store._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                scopes = sorted({scope, *(s.id for s in sessions)})
                held = db.execute(
                    "SELECT COALESCE(SUM(dollars),0) FROM reasoning_reservations WHERE scope IN ("
                    + ",".join("?" for _ in scopes)
                    + ")",
                    scopes,
                ).fetchone()[0]
                if spent + held + estimate > self.policy_getter()["max_agent_dollars"]:
                    return None, True
                db.execute(
                    "INSERT INTO reasoning_reservations VALUES(?,?,?,?)",
                    (identity, scope, estimate, time.time()),
                )
            response = await self._call_api(session, system, tools)
            if response is not None:
                self._track_spend(session, response)
                current = self.store.get_session(session.id)
                if current:
                    current.spend = session.spend
                    self.store.save_session(current)
                with self.store._connect() as db:
                    db.execute(
                        "DELETE FROM reasoning_reservations WHERE id=?", (identity,)
                    )
            return response, False

    async def _call_api(self, session: AgentSession, system: str, tools: list[ToolDef]):
        client = self._client()
        try:
            delay = 2.0
            spec = self._specs[session.agent.value]
            options = {}
            if spec.cache:
                options["cache_control"] = {"type": "ephemeral"}
            if spec.effort:
                options["output_config"] = {"effort": spec.effort}
            for attempt in range(3):
                try:
                    return await client.messages.create(
                        model=session.model,
                        max_tokens=spec.max_tokens,
                        system=system,
                        tools=[t.to_api() for t in tools],
                        messages=session.messages,
                        **options,
                    )
                except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                    status = getattr(e, "status_code", None)
                    if status and 400 <= status < 500 and status != 429:
                        raise  # our bug — don't retry blindly
                    logger.warning("API error (attempt %d): %s", attempt + 1, e)
                    if attempt < 2:
                        await asyncio.sleep(delay)
                    delay *= 3
            return None
        finally:
            await client.close()

    # ─── Bookkeeping ─────────────────────────────────────────────────────

    def _wake_text(self, session: AgentSession, events: list[Event]) -> str:
        lines = [f"[wake] {time.strftime('%A %Y-%m-%d %H:%M %Z', time.localtime())}"]
        for e in events:
            desc = e.payload.get("description") or e.kind
            lines.append(
                f"- event: {e.kind} — {desc}"
                if desc != e.kind
                else f"- event: {e.kind}"
            )
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
                session.model,
                s.input_tokens,
                s.output_tokens,
                s.cache_creation_input_tokens,
                s.cache_read_input_tokens,
                session.updated_at,
            )
            s.entries.append(
                {
                    "ts": session.updated_at,
                    "model": session.model,
                    "input_tokens": s.input_tokens,
                    "output_tokens": s.output_tokens,
                    "cache_creation_input_tokens": s.cache_creation_input_tokens,
                    "cache_read_input_tokens": s.cache_read_input_tokens,
                    "rates": legacy_rates,
                    "cost": legacy_cost,
                    "legacy_aggregate": True,
                }
            )
            s.dollars = legacy_cost
        s.input_tokens += inp
        s.output_tokens += out
        s.cache_creation_input_tokens += cache_write
        s.cache_read_input_tokens += cache_read
        call_cost, rates = calculate_usage_cost(
            session.model, inp, out, cache_write, cache_read, time.time()
        )
        s.entries.append(
            {
                "ts": time.time(),
                "model": session.model,
                "input_tokens": inp,
                "output_tokens": out,
                "cache_creation_input_tokens": cache_write,
                "cache_read_input_tokens": cache_read,
                "rates": rates,
                "cost": call_cost,
            }
        )
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
        session.messages = [
            {
                "role": "user",
                "content": "(earlier context was trimmed to stay within limits — your journal "
                "and memory notes are the durable record; re-read them if unsure)",
            },
            {"role": "assistant", "content": "Understood."},
        ] + msgs[cut:]


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

"""Versioned agent profiles: one place that says how each agent role runs.

A role (Discovery, the Fetch scout, its pick reviewer and its escalation,
Media, the Librarian, the subtitle manager and its checkers) resolves to a
Profile: the model, the reasoning effort actually sent to the provider
(nothing when the model takes no effort parameter, such as Haiku 4.5), call
and output bounds, spend bounds, and digests of the code that writes the
role's standing prompt and tools. Its version is a short hash of all of
that, so a session and every ledger row say exactly what they ran under.

Where a value comes from, most specific first:

1. ``SPARROW_PROFILES``: JSON ``{role: {field: value}}`` for evaluations,
   e.g. ``{"fetch_escalation": {"effort": "medium", "max_steps": 20}}``.
2. The per-feature variables: ``SPARROW_FETCH_SCOUT=off`` (Fetch smart
   throughout), ``SPARROW_SUBTITLE_MODEL`` / ``_EFFORT`` / ``_CONTRACTOR`` /
   ``_CONTRACTOR_EFFORT`` / ``_VERIFIER`` / ``_BUDGET``.
3. ``SPARROW_SMART_MODEL`` / ``SPARROW_CHEAP_MODEL``, then the saved config.
4. The defaults below. They match what ran before profiles existed.

Profiles bound calls and output; they never hold money. Spend is counted
per request or subscription across every session and profile, so a changed
profile or a new session never resets an allowance.

    python -m backend.agents.profiles [--json]
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from typing import Optional

logger = logging.getLogger("sparrow.agents.profiles")

DEFAULT_SMART_MODEL = "claude-sonnet-5"
DEFAULT_CHEAP_MODEL = "claude-haiku-4-5"

# Anthropic models that accept output_config.effort, and the levels beyond
# low/medium/high each accepts (platform.claude.com/docs/en/build-with-claude/effort,
# checked 2026-11-23). Haiku 4.5 and Sonnet 4.5 take no effort parameter.
_XHIGH = {"fable-5-2", "mythos-5-2", "fable-5-1", "mythos-5-1", "fable-5", "mythos-5", "opus-5-5", "opus-5",
          "opus-4-8", "opus-4-7", "sonnet-5-5", "sonnet-5", "haiku-5-5"}
_MAX = _XHIGH | {"mythos-preview", "opus-4-6", "sonnet-4-6"}
ANTHROPIC_EFFORT = {name: ("low", "medium", "high") + (("xhigh",) if name in _XHIGH else ()) + (("max",) if name in _MAX else ())
                    for name in _MAX | {"opus-4-5"}}
# OpenAI reasoning models take reasoning.effort; the levels Sparrow sends.
OPENAI_EFFORT = ("low", "medium", "high")
OPENAI_REASONING = re.compile(r"^(gpt-[56]|o[134])")

# Fields an operator may override per role in SPARROW_PROFILES.
OVERRIDABLE = {"model": str, "effort": str, "max_steps": int, "max_tokens": int, "max_dollars": float,
               "note_only_limit": int, "idle_backoff": float, "wave_dollars": float, "wave_calls": int,
               "waves": int, "title_letters": int}
# Bounds added after versions were first recorded: left out of a version's
# hash while zero, so roles that never use them keep the version they had.
LATER_BOUNDS = ("title_letters",)


@dataclass(frozen=True)
class Role:
    """A role's defaults. tier names how its model is chosen."""

    kind: str  # the AgentKind whose sessions run it
    tier: str  # smart | cheap | fetch | subtitle_manager | subtitle_checker | subtitle_verifier | subtitle_reader
    about: str
    effort: str = ""  # requested; "" is the provider's default (nothing sent)
    openai_effort: Optional[str] = None  # a different default request for OpenAI models
    max_steps: int = 40  # model calls per wake (or per structured check)
    max_tokens: int = 4096  # 0: the provider's own limit (nothing sent)
    max_dollars: float = 0.0  # allowance per budget scope; 0 is the household policy
    cache: bool = False
    note_only_limit: int = 0  # idle turns before timer wakes back off; 0 never
    idle_backoff: float = 0.0  # seconds of the first back-off, doubling
    wave_dollars: float = 0.0  # smart-model dollars per escalation wave (a ceiling); 0 unbounded
    wave_calls: int = 0  # smart-model calls per escalation wave, the same on any provider; 0 unbounded
    waves: int = 0  # escalation waves a request may open without the person asking
    title_letters: int = 0  # the shortest TMDB episode title (letters) matched against pack file names; 0 off
    prompt: tuple = ()  # module:attribute sources of the standing prompt
    tools: tuple = ()  # and of the tools


_FETCH_TOOLS = (
    "backend.agents.node_tools:acquisition_tools", "backend.agents.tools:fetch_tools", "backend.agents.tools:tmdb_tools",
    "backend.agents.tools:memory_tools", "backend.agents.tools:journal_tool", "backend.agents.tools:wake_tool",
    "backend.agents.tools:Toolbox.index_search", "backend.agents.scout", "backend.agents.service:AgentService._register_specs",
)
_MEDIA_TOOLS = (
    "backend.agents.node_tools:storage_tools", "backend.agents.tools:media_tools", "backend.agents.tools:tmdb_tools",
    "backend.agents.tools:memory_tools", "backend.agents.tools:journal_tool", "backend.agents.tools:wake_tool",
    "backend.agents.service:AgentService._register_specs",
)
_SUBTITLE_BUDGET = "SPARROW_SUBTITLE_BUDGET"

ROLES: dict[str, Role] = {
    "discovery": Role(
        "discovery", "cheap", "Discovery: finds and inspects titles for a description; no acquisition authority",
        max_steps=8, max_tokens=1400,
        prompt=("backend.agents.discovery:Discovery.register",), tools=("backend.agents.discovery:Discovery.tools",)),
    "fetch_scout": Role(
        "fetch", "fetch", "Fetch: the cheap model reads the scout's short list and proposes one row",
        note_only_limit=2, idle_backoff=3600, title_letters=8,
        prompt=("backend.agents.prompts:fetch_system", "backend.agents.prompts:SCOUT_WORKFLOW",
                "backend.agents.prompts:SHARED_RULES"), tools=_FETCH_TOOLS),
    "fetch_review": Role(
        "fetch", "smart", "Fetch: the smart model approves or vetoes the proposed row in one structured call",
        effort="low", max_steps=1, max_tokens=8000,
        prompt=("backend.agents.acquisition_review:REVIEW_SYSTEM", "backend.agents.acquisition_review:record"),
        tools=("backend.agents.acquisition_review:REVIEW_SCHEMA", "backend.agents.acquisition_review:review")),
    "fetch_escalation": Role(
        "fetch", "smart", "Fetch: the smart model searches with the raw tools after the scout gave up",
        # Calls bound a wave on any provider (a long context made one Sonnet call
        # cost about the old $0.02 cap); dollars are only a ceiling for huge contexts.
        note_only_limit=2, idle_backoff=3600, wave_calls=4, wave_dollars=0.25, waves=3,
        prompt=("backend.agents.prompts:fetch_system", "backend.agents.prompts:MANUAL_WORKFLOW",
                "backend.agents.prompts:SHARED_RULES"), tools=_FETCH_TOOLS),
    "media": Role(
        "media", "cheap", "Media: probes, names, places and records a landed download",
        note_only_limit=2, idle_backoff=3600,
        prompt=("backend.agents.prompts:media_system", "backend.agents.prompts:SHARED_RULES"), tools=_MEDIA_TOOLS),
    "media_escalation": Role(
        "media", "smart", "Media after escalate_model: conflicting evidence or ambiguous numbering",
        note_only_limit=2, idle_backoff=3600,
        prompt=("backend.agents.prompts:media_system", "backend.agents.prompts:SHARED_RULES"), tools=_MEDIA_TOOLS),
    "librarian": Role(
        "librarian", "cheap", "Librarian: one person's subscription, eligible gaps and opted-in upgrades",
        max_steps=6, max_tokens=1400,
        prompt=("backend.agents.curation:Curation.system_prompt",), tools=("backend.agents.curation:Curation.register",)),
    "subtitle_manager": Role(
        "subtitle", "subtitle_manager", "Subtitles: the manager reviews flagged pages and approves a track",
        effort="medium", max_steps=40, max_tokens=16000, cache=True,
        prompt=("backend.agents.subtitle_review:SYSTEM", "backend.agents.subtitle_review:MANAGER_SYSTEM",
                "backend.agents.subtitle_review:VERIFY_SYSTEM"), tools=("backend.agents.subtitles:Subtitles.attach",)),
    "subtitle_checker": Role(
        "subtitle", "subtitle_checker", "Subtitles: the cheap page checker reads every page",
        openai_effort="low", max_steps=1, max_tokens=8000,
        prompt=("backend.agents.subtitle_contract:TRANSLATE_SYSTEM", "backend.agents.subtitle_contract:COMPARE_SYSTEM"),
        tools=("backend.agents.subtitle_contract:check_pages",)),
    "subtitle_verifier": Role(
        "subtitle", "subtitle_verifier", "Subtitles: the second opinion on a borderline page's flags",
        openai_effort="low", max_steps=1, max_tokens=8000,
        prompt=("backend.agents.subtitle_contract:VERIFY_SYSTEM",), tools=("backend.agents.subtitle_contract:check_pages",)),
    "subtitle_reader": Role(
        "subtitle", "subtitle_reader", "Subtitles: the vision model reads picture (Blu-ray/DVD) subtitles",
        effort="low", max_steps=1, max_tokens=0,  # the provider's own output limit
        prompt=("backend.agents.subtitle_contract:READ_SYSTEM",), tools=("backend.agents.subtitle_contract:read_pictures",)),
}
# Which ledger phases the subtitle checkers' rows belong to.
SUBTITLE_PHASE_ROLES = {"subtitle_check": "subtitle_checker", "subtitle_second_opinion": "subtitle_verifier",
                        "picture_read": "subtitle_reader"}


@dataclass(frozen=True)
class Profile:
    id: str
    kind: str
    tier: str
    model: str
    effort: str  # requested
    effort_sent: str  # what the provider receives; "" sends nothing
    max_steps: int
    max_tokens: int
    max_dollars: float
    cache: bool
    note_only_limit: int
    idle_backoff: float
    wave_dollars: float
    wave_calls: int
    waves: int
    prompt: str  # version of the standing prompt's source
    tools: str  # version of the tools' source
    title_letters: int = 0
    version: str = ""

    def contents(self) -> dict:
        data = asdict(self)
        data.pop("version")
        for name in LATER_BOUNDS:
            if not data.get(name):
                data.pop(name, None)
        return data

    def to_dict(self) -> dict:
        return asdict(self)

    def bind(self, model: str) -> "Profile":
        """The same role on the model a session actually runs (a session keeps
        the model it was created with, or escalated to)."""
        if not model or model == self.model:
            return self
        if self.id in ROLES and self.tier != "spec":
            return resolve(self.id, model=model)
        return _versioned(replace(self, model=model, effort_sent=effort_for(model, self.effort), version=""))


def _versioned(profile: Profile) -> Profile:
    text = json.dumps(profile.contents(), sort_keys=True, default=str)
    return replace(profile, version=hashlib.sha256(text.encode()).hexdigest()[:12])


def is_openai(model: str) -> bool:
    from .openai_loop import is_openai_model

    return is_openai_model(model)


def _anthropic_name(model: str) -> str:
    name = re.sub(r"^(?:anthropic\.)?claude-", "", (model or "").lower())
    return re.sub(r"-(?:\d{8}|latest)$", "", name)


def effort_for(model: str, requested: str) -> str:
    """The effort to send for a request, or "" when the model takes none
    (or none at that level): Sparrow never sends a parameter a model refuses."""
    requested = (requested or "").strip().lower()
    if not requested:
        return ""
    if is_openai(model):
        return requested if OPENAI_REASONING.match((model or "").lower()) and requested in OPENAI_EFFORT else ""
    return requested if requested in ANTHROPIC_EFFORT.get(_anthropic_name(model), ()) else ""


# ─── Resolution ───────────────────────────────────────────────────────────


def tier_model(tier: str, cfg=None) -> str:
    """The configured smart or cheap model: environment, then saved config, then default."""
    if tier == "smart":
        return os.getenv("SPARROW_SMART_MODEL") or getattr(cfg, "smart_model", "") or DEFAULT_SMART_MODEL
    if tier == "cheap":
        return os.getenv("SPARROW_CHEAP_MODEL") or getattr(cfg, "cheap_model", "") or DEFAULT_CHEAP_MODEL
    if tier == "fetch":
        # The Fetch Agent starts cheap, working through the scout with its picks
        # reviewed; SPARROW_FETCH_SCOUT=off keeps it on the smart model throughout.
        return tier_model("smart" if os.getenv("SPARROW_FETCH_SCOUT", "").lower() == "off" else "cheap", cfg)
    from . import subtitles

    return {
        "subtitle_manager": subtitles.review_model,
        "subtitle_checker": subtitles.contractor_model,
        "subtitle_verifier": subtitles.verifier_model,
        "subtitle_reader": subtitles.reader_model,
    }[tier]()


_warned: set = set()


def overrides() -> dict:
    """SPARROW_PROFILES, validated: unknown roles, fields and bad values are
    ignored (once logged), never fatal."""
    raw = os.getenv("SPARROW_PROFILES", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except ValueError as exc:
        _warn(f"SPARROW_PROFILES is not a JSON object ({exc}); ignored.")
        return {}
    out = {}
    for role, fields in data.items():
        if role not in ROLES or not isinstance(fields, dict):
            _warn(f"SPARROW_PROFILES: no role named {role!r}; ignored.")
            continue
        for name, value in fields.items():
            kind = OVERRIDABLE.get(name)
            try:
                if kind is None or isinstance(value, bool) or value is None:
                    raise ValueError
                value = kind(value)
                if kind is not str and value < 0:
                    raise ValueError
            except (TypeError, ValueError):
                _warn(f"SPARROW_PROFILES: {role}.{name}={value!r} is not a valid setting; ignored.")
                continue
            out.setdefault(role, {})[name] = value
    return out


def _warn(text):
    if text not in _warned:
        _warned.add(text)
        logger.warning(text)


def resolve(role: str, cfg=None, model: str = "") -> Profile:
    """One role's profile as it would run now (on model, when given: the one a
    session actually runs)."""
    spec = ROLES[role]
    custom = overrides().get(role, {})
    model = model or custom.get("model") or tier_model(spec.tier, cfg)
    effort = _requested_effort(role, spec, model)
    if "effort" in custom:
        effort = custom["effort"]
    fields = {
        "max_steps": spec.max_steps, "max_tokens": spec.max_tokens, "max_dollars": _max_dollars(spec),
        "note_only_limit": spec.note_only_limit, "idle_backoff": spec.idle_backoff,
        "wave_dollars": spec.wave_dollars, "wave_calls": spec.wave_calls, "waves": spec.waves,
        **{name: getattr(spec, name) for name in LATER_BOUNDS},
    }
    fields.update({k: v for k, v in custom.items() if k in fields})
    return _versioned(Profile(
        id=role, kind=spec.kind, tier=spec.tier, model=model, effort=effort, effort_sent=effort_for(model, effort),
        cache=spec.cache, prompt=digest(spec.prompt), tools=digest(spec.tools), **fields,
    ))


def _requested_effort(role, spec, model):
    if role == "subtitle_manager":
        return os.getenv("SPARROW_SUBTITLE_EFFORT") or spec.effort
    if role in ("subtitle_checker", "subtitle_verifier"):
        configured = os.getenv("SPARROW_SUBTITLE_CONTRACTOR_EFFORT", "")
        if configured:
            return configured
    if spec.openai_effort is not None and is_openai(model):
        return spec.openai_effort
    return spec.effort


def _max_dollars(spec):
    if spec.kind == "subtitle":
        try:
            return float(os.getenv(_SUBTITLE_BUDGET) or 0)
        except ValueError:
            return 0.0
    return spec.max_dollars


def role_for(session, cfg=None) -> str:
    """The role a session runs now: a Fetch session on the escalation model
    (while the scout is on) has escalated; so has a Media session on the smart one."""
    kind = getattr(session.agent, "value", session.agent)
    if kind == "fetch":
        escalated = resolve("fetch_escalation", cfg).model
        return "fetch_escalation" if session.model == escalated and resolve("fetch_scout", cfg).model != escalated \
            else "fetch_scout"
    if kind == "media":
        escalated = resolve("media_escalation", cfg).model
        return "media_escalation" if session.model == escalated and resolve("media", cfg).model != escalated else "media"
    return {"subtitle": "subtitle_manager"}.get(kind, kind)


def for_session(session, cfg=None) -> Profile:
    return resolve(role_for(session, cfg), cfg, model=session.model)


def from_spec(spec, model: str = "") -> Profile:
    """A profile for an agent kind registered without one (trials and tests):
    its own fields, under the kind's name."""
    model = model or spec.model()
    return _versioned(Profile(
        id=spec.kind, kind=spec.kind, tier="spec", model=model, effort=spec.effort,
        effort_sent=effort_for(model, spec.effort), max_steps=spec.max_steps, max_tokens=spec.max_tokens,
        max_dollars=spec.max_dollars, cache=spec.cache, note_only_limit=0, idle_backoff=0.0, wave_dollars=0.0, wave_calls=0, waves=0,
        prompt="", tools="",
    ))


def all_profiles(cfg=None) -> list[Profile]:
    return [resolve(role, cfg) for role in ROLES]


# ─── Prompt and tool versions ─────────────────────────────────────────────


def digest(sources: tuple) -> str:
    return _digest(tuple(sources)) if sources else ""


@lru_cache(maxsize=None)
def _digest(sources: tuple) -> str:
    """A short hash of the code and text a role's prompt or tools come from:
    it changes when that source changes. Without source files (a frozen
    package) the compiled code stands in."""
    hashed = hashlib.sha256()
    for source in sources:
        hashed.update(source.encode())
        hashed.update(_source_text(source).encode())
    return hashed.hexdigest()[:10]


def _source_text(source: str) -> str:
    module_name, _, path = source.partition(":")
    try:
        value = importlib.import_module(module_name)
        for part in filter(None, path.split(".")):
            value = getattr(value, part)
    except (ImportError, AttributeError):
        return "missing"
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, default=str)
    try:
        return inspect.getsource(value)
    except (OSError, TypeError):
        code = getattr(value, "__code__", None)
        return repr((code.co_code, code.co_consts, code.co_names)) if code else repr(value)


def table(profiles: list[Profile]) -> str:
    lines = ["| Role | Model | Effort sent | Calls | Output | Bounds | Prompt | Tools | Version |",
             "|---|---|---|---|---|---|---|---|---|"]
    for p in profiles:
        bounds = []
        if p.max_dollars:
            bounds.append(f"${p.max_dollars:g} allowance")
        if p.wave_calls or p.wave_dollars:
            per = " and ".join(x for x in (f"{p.wave_calls} calls" if p.wave_calls else "",
                                            f"${p.wave_dollars:g}" if p.wave_dollars else "") if x)
            bounds.append(f"{per} per wave × {p.waves}")
        if p.note_only_limit:
            bounds.append(f"idle after {p.note_only_limit}")
        if p.title_letters:
            bounds.append(f"episode titles of {p.title_letters}+ letters matched in pack files")
        effort = "-" if not p.model else p.effort_sent or (f"none ({p.effort} not accepted)" if p.effort else "none (default)")
        lines.append(f"| {p.id} | {p.model or '(off)'} | {effort} | {p.max_steps} | {p.max_tokens or 'default'} | "
                     f"{', '.join(bounds) or '-'} | {p.prompt} | {p.tools} | {p.version} |")
    return "\n".join(lines)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="python -m backend.agents.profiles",
                                     description="Each agent role's resolved profile (environment and defaults).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    resolved = all_profiles()
    print(json.dumps([p.to_dict() for p in resolved], indent=1) if args.json else table(resolved))


if __name__ == "__main__":
    sys.exit(main())

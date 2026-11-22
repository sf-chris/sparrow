"""The escalated Fetch search's bounded allowance.

Once the cheap scout hands a request to the smart model, its searching comes
in waves. A wave may make the Fetch escalation profile's ``wave_calls``
smart-model calls (the same on any provider), within ``wave_dollars`` as a
ceiling: soft caps, checked before each search, so the call that crosses one
is never cut short. Another wave opens only for

- a genuinely new candidate: a plausible copy (by the scout's own judge) that
  neither the scout nor an earlier wave had seen,
- an identity conflict the agent records (record_identity_conflict), such as
  TMDB's episode title against a release's numbering,
- a chosen download failing (a stall) or a Media Agent report, or
- the person asking for a check (nudge, resume), which starts the count again.

At most ``waves`` open without the person. The tool layer enforces it: a
search past the allowance is refused with an honest ToolError, and a second
refused call ends the turn until something changes. Every decision is a
ledger enforcement row (escalation_wave, escalation_bound, identity_conflict).
The request's own allowance still applies on top: this only bounds how much
of it smart-model searching may use.
"""

from __future__ import annotations

import time

from .models import CaseState
from .runtime import ToolError

USER_CHECK = {"nudge", "resume"}
NEW_FACTS = {"download_stalled": "download_failed", "media_report": "media_report"}
TURN_ENDS_AFTER = 2  # refused model calls in one wave
PAUSED = "Searched as far as allowed for now. Ask for a check to keep looking."


def bound(runtime, session):
    """The session's profile when it carries an escalation allowance, else None."""
    profile_for = getattr(runtime, "profile_for", None)
    profile = profile_for(session) if profile_for else None
    limited = profile is not None and (getattr(profile, "wave_dollars", 0) > 0 or getattr(profile, "wave_calls", 0) > 0)
    return profile if limited else None


def _fresh(session, memo, reason) -> dict:
    memo = memo or {}
    rows = (memo.get("rows") or {}).values()
    return {
        "wave": 1, "opened": time.time(), "spent_at": session.spend.dollars, "reason": reason,
        "known": sorted({r.get("info_hash") for r in rows if r.get("info_hash")}),
        "found": [], "conflicts": [], "refused_calls": [],
        "titles": [str(t) for t in (memo.get("titles") or [])][:3],
        "exclude": [str(t) for t in (memo.get("exclude") or [])][:20],
    }


def begin(session, profile, memo) -> dict:
    """escalate_model: the first wave, knowing every row the scout listed."""
    state = session.controls["escalation"] = _fresh(session, memo, "escalated")
    return {"rule": "escalation_wave", "detail": {"wave": 1, "reason": "escalated", "cap": profile.wave_dollars,
                                                  "waves": profile.waves, "known": len(state["known"])}}


def current(session, memo=None) -> dict:
    state = session.controls.get("escalation")
    if not state:
        # Escalated before waves were tracked: its allowance starts now.
        state = session.controls["escalation"] = _fresh(session, memo, "upgrade")
    return state


def spent(session, state, asking=True) -> float:
    """Smart-model dollars this wave. asking: a tool is running for the latest
    call, which is already paid for; the cap governs the work that follows."""
    total = session.spend.dollars - float(state.get("spent_at") or 0)
    last = session.spend.entries[-1] if session.spend.entries else None
    if asking and last and not last.get("purpose") and float(last.get("ts") or 0) >= float(state.get("opened") or 0):
        total -= float(last.get("cost") or 0)
    return max(0.0, total)


def calls(session, state, asking=True) -> int:
    """Smart-model calls this wave, not counting the one whose tool is running."""
    opened = float(state.get("opened") or 0)
    made = [e for e in session.spend.entries if not e.get("purpose") and float(e.get("ts") or 0) >= opened]
    return max(0, len(made) - (1 if asking and made else 0))


def _open(session, state, reason, used, cap) -> dict:
    state["known"] = sorted(set(state["known"]) | set(state["found"]))
    state.update(wave=int(state["wave"]) + 1, opened=time.time(), spent_at=session.spend.dollars, reason=reason,
                 found=[], refused_calls=[])
    return {"rule": "escalation_wave", "detail": {"wave": state["wave"], "reason": reason,
                                                  "previous_wave_spent": round(used, 5), "cap": cap}}


async def guard(tb, ctx):
    """Before an escalated search: allow it, open the next wave, or refuse."""
    profile = bound(ctx.runtime, ctx.session)
    if not profile:
        return None
    state = current(ctx.session, tb.scouted.get(ctx.session.id))
    cap, used = profile.wave_dollars, spent(ctx.session, state)
    limit, made = profile.wave_calls, calls(ctx.session, state)
    if (not cap or used < cap) and (not limit or made < limit):
        return state
    pending = next((c for c in state["conflicts"] if not c.get("used")), None)
    reason = "new_candidate" if state["found"] else "identity_conflict" if pending else ""
    if reason and state["wave"] < profile.waves:
        if reason == "identity_conflict":
            pending["used"] = True
        ctx.facts.setdefault("enforced", []).append(_open(ctx.session, state, reason, used, cap))
        return state
    current_call = getattr(ctx.runtime, "current_call", None)
    call = current_call(ctx.session.id) if current_call else ""
    if call not in state["refused_calls"]:
        state["refused_calls"].append(call)
    stop = len(state["refused_calls"]) >= TURN_ENDS_AFTER
    ctx.facts.setdefault("enforced", []).append({"rule": "escalation_bound", "detail": {
        "wave": state["wave"], "waves": profile.waves, "spent": round(used, 5), "cap": cap,
        "calls": made, "call_limit": limit, "ended_turn": stop}})
    if stop:
        await _pause(tb, ctx)
    if state["wave"] >= profile.waves:
        why = f" and all {profile.waves} search waves are used."
        options = ("Add the best copy already found with client_add (the Media Agent verifies what lands), wake_me to "
                   "wait for new releases, or job_close as abandoned with a plain note. The person can ask for another search.")
    else:
        why = " and found no new candidate."
        options = ("Another wave needs a genuinely new candidate or an identity conflict you record with "
                   "record_identity_conflict. Otherwise add the best copy already found with client_add (the Media Agent "
                   "verifies what lands), wake_me to wait, or job_close as abandoned with a plain note.")
    allowance = (f"made {made} of its {limit} searching calls" if limit and made >= limit
                 else f"spent ${used:.3f} of its ${cap:.2f} allowance")
    raise ToolError(
        f"Search refused: this escalated search wave has {allowance}{why} {options}"
        + (" This turn has ended; the request waits for news or the person's check." if stop else "")
    )


async def _pause(tb, ctx):
    if ctx.hibernate:
        return  # wake_me already ran in this batch: its timer and reason stand
    ctx.hibernate = True
    ctx.session.wake_at = 0.0
    ctx.session.wake_reason = PAUSED
    ctx.session.outcome = CaseState.NEEDS_INPUT
    job = tb.job_for(ctx.session)
    if job:
        job.state_line, job.next_wake_at = PAUSED, 0.0
        tb.store.save_job(job)
        await tb.broadcast({"type": "job_update", "data": job.to_dict()})


def note(tb, ctx, state, rows) -> None:
    """After an escalated search: remember plausible copies nobody had seen."""
    from . import scout
    from .node_tools import missing_episodes

    job = tb.job_for(ctx.session)
    if not job or not rows:
        return
    try:
        targets = missing_episodes(tb, job)[:12]
    except Exception:
        targets = sorted((int(s), int(e)) for s, eps in job.wanted_episodes.items() for e in eps)[:12]
    titles = state.get("titles") or [job.title]
    seen = set(state["known"]) | set(state["found"])
    new = 0
    for row in rows:
        try:
            candidate = scout.judge(row, job, targets, titles, state.get("exclude") or ())
        except Exception:
            candidate = None
        if candidate and candidate["info_hash"] and candidate["info_hash"] not in seen:
            seen.add(candidate["info_hash"])
            state["found"].append(candidate["info_hash"])
            new += 1
    ctx.facts["new_candidates"] = new


async def record_conflict(tb, ctx, args) -> str:
    tb.require_authority(ctx)
    profile = bound(ctx.runtime, ctx.session)
    if not profile:
        raise ToolError("Identity conflicts are recorded during an escalated search; this request is not in one.")
    conflict = " ".join(str(args.get("conflict") or "").split())[:400]
    evidence = " ".join(str(args.get("evidence") or "").split())[:600]
    if len(conflict) < 12 or len(evidence) < 12:
        raise ToolError("Say what disagrees (for example TMDB's episode title against a release's numbering) and the "
                        "evidence: the rows, files or listings you saw.")
    state = current(ctx.session, tb.scouted.get(ctx.session.id))
    key = conflict.lower()
    if any(c.get("key") == key for c in state["conflicts"]):
        raise ToolError("This conflict is already recorded. Act on what you found, or record a different conflict.")
    entry = {"key": key, "evidence": evidence, "wave": state["wave"], "ts": time.time(), "used": False}
    state["conflicts"].append(entry)
    records = [{"rule": "identity_conflict", "detail": {"conflict": conflict, "evidence": evidence, "wave": state["wave"]}}]
    cap, used = profile.wave_dollars, spent(ctx.session, state)
    if used < cap:
        message = f"Recorded. This wave has ${cap - used:.3f} left; the conflict opens the next wave when it runs out."
    elif state["wave"] < profile.waves:
        entry["used"] = True
        records.append(_open(ctx.session, state, "identity_conflict", used, cap))
        message = (f"Recorded. Search wave {state['wave']} of {profile.waves} is open (${cap:.2f}): search for what "
                   "settles the conflict, then act on it.")
    else:
        message = (f"Recorded, but all {profile.waves} search waves are used. Add the best copy already found, wake_me, "
                   "or close as abandoned; the person can ask for another search.")
    ctx.facts.setdefault("enforced", []).extend(records)
    return message


def woken(runtime, session, events) -> list:
    """A wake's events: the person's check starts the count again; a failed
    download or a Media report opens the next wave once this one is spent."""
    profile = bound(runtime, session)
    state = session.controls.get("escalation")
    if not profile or not state:
        return []
    kinds = [e.kind for e in events]
    used = spent(session, state, asking=False)
    if USER_CHECK & set(kinds):
        state["wave"] = 0
        return [_open(session, state, "user_check", used, profile.wave_dollars)]
    reason = next((NEW_FACTS[k] for k in kinds if k in NEW_FACTS), "")
    if reason and used >= profile.wave_dollars and state["wave"] < profile.waves:
        return [_open(session, state, reason, used, profile.wave_dollars)]
    return []

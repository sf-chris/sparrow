# Retrievable agent observations — issue #3, P4

This follow-up to merged [PR #5](https://github.com/sf-chris/sparrow/pull/5)
addresses oversized tool observations. It does not complete P4 or the parent issue.

Previously the runtime silently discarded tool-result text after character
40,000. Fetch also dropped search candidates after 60 rows and torrent files
after 200; Discovery dropped collection matches after 50, and Librarian evidence
dropped eligible candidates after 100. Those four local row caps are removed.
Source responses remain observations of what the provider returned, not proof
that a search exhausted every possible source or provider page.

## Storage and retrieval contract

- Results up to 40,000 characters remain unchanged in the durable invocation
  receipt. Larger results are saved as immutable observations in `agent_evidence`
  in the existing SQLite database, before a preview is returned. The observation,
  invocation receipt and session snapshot commit in one write transaction.
- Each preview identifies the observation and original invocation, timestamp,
  request revision, original error status, UTF-8 byte size, character count and
  SHA-256. `artifact_complete` describes the saved observation; `complete`
  describes whether the returned page contains the whole observation. Neither
  field declares a viewing request complete.
- Every agent receives `evidence_read` for bounded character pages and
  `evidence_list` to rediscover saved references after context loss. Pages use
  zero-based character offsets with an exclusive `end`; concatenate their `text`
  in order to reconstruct the exact original tool result, including Unicode.
  Page text is at most 6,000 characters, keeping even escaped control characters
  within the existing result allowance. Lists return at most 20 observations
  with a continuation cursor.
- Access requires the same session, person and job. The model cannot supply a
  different session or owner. Normal runtime authority checks apply before a
  fresh retrieval invocation; stale observations retain their original revision
  and never grant authority. Acquisition and publication receipts still govern
  actual effects. Diagnostics advertise the same tool set the runtime uses.
- Artifact payload limits are 8 MiB per observation, 32 MiB per session and
  512 MiB across the server. The quota check and insert share a serialized SQLite
  write transaction. These are limits on the new archive payload, not on the
  whole database or pre-existing invocation history. Existing observations are
  never silently evicted or overwritten.
- If a limit is exceeded, the result explicitly reports unavailable full
  evidence, a partial preview and an error. It tells the agent to request narrower
  evidence and inspect receipts before repeating a potentially completed effect.
  Control receipts are preserved. Archive or receipt write failures roll back
  together; existing recovery treats the invocation as uncertain.
- Schema generation `agent-evidence-1` takes the existing migration backup before
  adding the archive. Previously truncated observations cannot be reconstructed
  retroactively.

## Verification and remaining scope

[Runtime evidence tests](../../tests/test_agent_evidence.py) exercise exact
reconstruction across restart, a tool loop inspecting a tail entry before
finishing, observer interruption, atomic rollback, session/owner isolation,
cancellation, original revisions, invalid ranges, escaped/error results, listing
after context loss, simultaneous quota consumers and migration backup. Source
regressions cover 85 search candidates, 250 torrent files, 55 collection matches
and 125 eligible episodes.

These are isolated, controlled tests with no paid model or acquisition calls.
[Verification](evidence-verification.json): `scripts/check.sh` passed all 154
enabled tests (156 total, two opt-in live tests skipped), compilation, dependency
consistency, frontend build, npm audit and release-tree checks. The Linux package
passed import, media ranges, converted segments, login/resume across restart, PWA
assets and bundled speech-model loading. A 105,009-character observation was
reconstructed exactly after restarting the actual container; packaged backend
source hashes matched the checked source. Sixteen new regression tests cover the
archive and removed row caps. The installed owner container was not changed.
Retrieval itself makes no provider call; choosing and reasoning over additional
pages consumes the same model-call/spend allowance as other tool use. Production
model defaults are unchanged; live quality and total-cost comparisons remain open.

P4 still needs versioned provider profiles/effort, measured escalation,
structured checkpoints and recoverable conversation compaction. The existing
history trimming behavior remains; the new listing recovers oversized observation
references, not the discarded conversation. Future work also includes explicit
cross-session handoffs, operator retention/reclamation policy and fuller source
pagination. Small historical results remain in invocation receipts, without a
new model-facing retrieval tool. The remaining issue #3 packages and live/device
acceptance remain in [the implementation ledger](IMPLEMENTATION.md).

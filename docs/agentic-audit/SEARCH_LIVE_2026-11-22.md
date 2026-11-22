# Live search benchmark, 22 November 2026 (issue #15)

`tests/evals/search_bench.py live` on main `8b25fb8`, 28 fixed titles (16 anime
series, 6 films, 6 western series), stopping each request once its download was
chosen; nothing was downloaded. Models: `cheap-model` scouting and `smart-model`
for review and escalation (the Provider key had no credit). Summary:
[`tests/evals/results/search-live-20261122.json`](../../tests/evals/results/search-live-20261122.json).
The indexer answers were recorded locally only; they are real listings.

| | This run | Target | Before (batch 3, Assistant, 2026-10-02) |
|---|---|---|---|
| Titles | 28 | | 20 |
| Total | $0.43 | | $5.19 |
| Median per title | 0.36¢ | ≤ 2¢ | 20¢ |
| p90 | 6.5¢ | ≤ 10¢ | — |
| Escalation rate | 11% | ≤ 15% | 80% |
| Rate-limited searches | 0 of 146 | | 134 of 255 |

The models differ between the two runs, so the before/after is not a model-only
comparison; the scout fixes moved most titles onto the cheap path, which is where
most of the change comes from. The audit agent's report follows.

## Ledger audit (smart-model, $0.0526)

The run meets all three aggregate search-cost targets, but three escalated titles account for $0.336 of $0.427 (79%). None reached the library, and subtitles cost $0, so this run does not establish that episodes or professional subtitles were delivered. The largest savings are in smart-model search loops, not indexer fees.

- **1. Bound smart-model search after a cheap scout has exhausted useful queries.** (~$0.166) — Mob Psycho 100 cost $0.1671 (46× the $0.0036 median), including $0.1657 escalated_search across 11 calls. After two scout waves, the smart model found the wanted file in a 12-file pack using “Mob Psycho 100 Season 1.” A broad season-pack query and file peek were useful; the extended smart-model loop was not evidently necessary.
  → Add “title Season 1” and selectable-file peeks to the cheap scout’s pre-escalation rules. Give escalations a $0.02 soft cap; require a new candidate or a documented identity conflict before another smart-model search wave. The phase cost is exposure, not a claim that all $0.1657 is recoverable.
- **2. Do not escalate solely to repeat a search and download a vetoed candidate.** (~$0.100) — Violet Evergarden cost $0.1041, including $0.1004 escalated_search. The reviewer rejected the only exact result because quality/audio were unverified; after further searches, the smart model added that same release for file verification. Four queries were also repeated live.
  → Make an “unverified but plausible” reviewer verdict trigger a bounded download-and-probe path, not a hard veto plus smart-model search. Deduplicate normalized queries across scout and escalation; require a genuinely new candidate before further searching. Keep the post-download quality, audio and subtitle checks.
- **3. Resolve episode-order disputes in code before paying for manual search.** (~$0.060) — Cowboy Bebop cost $0.0648, including $0.0600 escalated_search. The reviewer correctly caught that the requested title, “Jamming with Edward,” is S01E09 in the torrent’s numbering rather than its S01E05; the eventual download selected the S01E09 file from a season pack. This was a necessary identity check, but not necessarily a $0.0600 search.
  → Compare requested episode titles against season-pack filenames and known alternate-order mappings before escalation. Preserve reviewer intervention for unresolved conflicts; once the title and file match, stop searching and select only that file.
- **4. Make review conditional, without removing its useful safeguards.** (~$0.068) — Reviews cost $0.0682 over 27 calls; 21 approved the scout pick. Reviews also chose another row four times and vetoed two picks, including the consequential Cowboy Bebop numbering issue. Violet and Cowboy Bebop each had a vetoed pick later chosen anyway; that outcome alone does not prove the original caution was wrong.
  → Auto-approve only high-confidence exact-title/year or episode-title matches with verified files and required attributes; retain review for packs with uncertain files, namesakes, episode-order conflicts and unknown quality/audio. Log whether a veto led to a different verified file or merely deferred verification.
- **5. Stop low-value wake and indexer churn; do not count waiting as billed search.** (~$0.000) — Mob’s flag reports three consecutive model calls that only wrote notes and slept. It and Violet waited 28 and 19 minutes for indexer allowance; Cowboy waited 10 minutes. The report records zero rate-limited searches and zero failed model calls, so neither failed-call retries nor billed rate-limit errors are evidenced.
  → Use event-driven download/status wakes and suppress note-only model turns. Queue and deduplicate indexer requests under one allowance scheduler. Measure the avoided model-turn dollars separately; elapsed waiting time is not itself a demonstrated dollar saving.

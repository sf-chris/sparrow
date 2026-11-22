# Agent evaluation contracts

`contracts-v1.json` fixes 30 ordinary/adversarial outcome checks across the five
current roles. `run_contracts.py` repeats them against isolated fixtures and
blocks creation of a real provider client. It records latency, outcomes and
source/manifest hashes. Run from the repository root with supported FFmpeg and
ffprobe on PATH (or `SPARROW_FFMPEG` / `SPARROW_FFPROBE` set):

```sh
.venv/bin/python tests/evals/run_contracts.py --runs 3 --output /tmp/sparrow-contracts.json
```

This is a controlled contract baseline. Scripted tool choices are not evidence
that a real model makes good decisions, and tool guardrail tests are not whole
agent evaluations. Use it before changing runtime/tool contracts. It does not
complete issue #3's live role suite, candidate comparisons or unseen holdout.

The opt-in Librarian evaluation now uses real personal subscription authority,
current tools and persisted jobs, with controlled catalogue/file observations.
It never starts a downloader or queries a real catalogue. Supply the provider
key in the environment, then explicitly enable paid model calls:

```sh
SPARROW_RUN_AGENT_EVALS=1 SPARROW_EVAL_RUNS=3 SPARROW_EVAL_REPORT=/tmp/sparrow-live-evals.jsonl .venv/bin/python -m unittest discover -s tests/evals
```

Each case starts fresh. Without `SPARROW_EVAL_REPORT`, live results append to
the ignored `tests/evals/artifacts/librarian-live.jsonl`. The report includes tool attempts/results, forbidden
attempts (even if blocked), final request scope, session state, latency and actual
recorded usage/estimated spend. Successful prose alone cannot pass. The offline
`test_eval_contracts.py` checks the same fixtures/tools and the attempted-scope
failure rule without a provider call. Keep live runs separate from controlled
results; do not choose new production defaults from the latter.

## Search benchmark (issue #15)

`search_bench.py` measures what finding a title costs, before and after a change
to the scout, prompts or models. `search-titles.json` fixes the title sets:
anime (reconstructed from the 2026-10 field-test and ledger-run commits; the exact
batch-1/batch-3 lists live only in those runs' state directories, pin them with
`search_bench.py titles --from-state DIR --name batch-3 --write`), films and
western TV.

Offline replay runs the real scout over cassettes in `cassettes/search/` with no
model call and reports whether the right copy ranks first, searches and peeks
used, and how often the cheap path would have to escalate. The bundled cassettes
are synthetic (realistic naming, illustrative seeders), so their numbers are a
projection about the scout, not a cost. It also projects whether code would let
the first row skip the pick review, and counts seasons whose episode order was
settled by title; a cassette may name the one right file of a pack
(`correct_files`). `results/search-offline.json` is the current baseline;
regenerate it with each scout change and compare:

```sh
.venv/bin/python tests/evals/search_bench.py offline --compare tests/evals/results/search-offline.json
.venv/bin/python tests/evals/search_bench.py offline --output tests/evals/results/search-offline.json
```

The live search-only mode makes paid model calls and real indexer and TMDB
searches, so it needs explicit consent and keys in the environment
(`PROVIDER_API_KEY` and/or `ALTPROVIDER_API_KEY`, `TMDB_API_KEY`; models from
`SPARROW_CHEAP_MODEL` / `SPARROW_SMART_MODEL`). It runs real Fetch Agents on a new
state directory under `artifacts/`, intercepts the transfer at the storage node (no
torrent is ever added), stops each request once its download is chosen, and
reports cost per title, median, p90 and escalation rate from the ledger against the
targets (median ≤ 2¢, p90 ≤ 10¢, escalation ≤ 15%). `--max-dollars` stops the run;
`--record DIR` saves real cassettes for later offline replays (mark their right
copies by hand). `--output` writes a summary without release names, safe to commit
under `results/`; the full result stays in the state directory.

```sh
SPARROW_RUN_LIVE_BENCH=1 .venv/bin/python tests/evals/search_bench.py live --max-dollars 5 \
  --record tests/evals/cassettes/search/live-$(date +%Y%m%d) --output tests/evals/results/search-live-$(date +%Y%m%d).json
.venv/bin/python -m backend.agents.ledger audit --data <state dir> --batch <batch>
```

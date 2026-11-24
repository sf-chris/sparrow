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
agent evaluations. Use it before changing runtime/tool contracts. The live role
suite and holdout are below.

## Live role evaluation (issue #3, P2)

`run_live.py` runs the same 30 cases (`live-v1.json`, same ids, roles and
contracts) and a separate unseen holdout of 12 (`live-holdout-v1.json`) through
the real Discovery, Fetch, Media, Librarian and subtitle agents, runtime, tools
and cost ledger with a real model. Only the model provider is real: TMDB and the
torrent index answer from each case's synthetic catalogue and index at the
network boundary, the download app records transfers without starting them, and
media files are placeholders whose probes return the case's facts (the subtitle
cases use the packaged FFmpeg on a 24-second fixture video, so set
`SPARROW_FFMPEG`/`SPARROW_FFPROBE` or they are skipped and listed). Any other
network request fails the run. Acceptance reads outcomes (requests, transfers and
the files they would fetch, recorded copies, subtitle tracks, sessions) and every
attempted tool call: a forbidden attempt fails a run even when a tool blocked it.
Prose is never judged.

Each case runs three times by default, each on a fresh household. Spending is
capped: once the cost ledger (or the provider meter at the network gate) reaches
`--max-dollars`, no further paid request is sent, runs in flight are cancelled
and nothing new starts; the summary lists every run that did not happen. Inside
each run the household's per-request allowance is `--per-run-dollars` ($1).

Free first, to check the harness and see the token estimate (scripted reference
answers, the network refused):

```sh
.venv/bin/python tests/evals/run_live.py --dry-run
```

The paid run, with the Z.ai key in the environment:

```sh
SPARROW_RUN_LIVE_EVALS=1 ZAI_API_KEY=... SPARROW_FFMPEG=... SPARROW_FFPROBE=... \
  .venv/bin/python tests/evals/run_live.py --max-dollars 15 \
  --output tests/evals/artifacts/live-$(date +%Y%m%d).json
```

The models are the defaults (`glm-5.3-flash` cheap, `glm-5.3` smart and subtitle
manager) unless `SPARROW_CHEAP_MODEL`, `SPARROW_SMART_MODEL` or
`SPARROW_SUBTITLE_*` say otherwise. All 126 runs cost $0.46 on 25 November 2026
(`docs/agentic-audit/GLM_BENCHMARK_2026-11-25.md`).
`SPARROW_PROFILES` overrides a role's model, effort or bounds for comparisons;
`--cases`, `--roles` and `--set main|holdout` narrow a run, `--keep-state` keeps
each run's state directory for `python -m backend.agents.ledger`.

Output: a JSON result (default `tests/evals/artifacts/live-<time>.json`, ignored
by git) with, per run, the outcome and each contract check, tool actions and
refusals, guardrail enforcements, requested and served models, profile ids,
versions and effort, calls, tokens, ledger cost and latency; and a markdown
summary beside it per role: pass rate per case over the repetitions, holdout pass
rate, cost per role and in total, median latency and each failure with the
contract it broke. Neither contains keys, release or file names, or agent prose.
Dry-run pass rates test the harness, not a model.

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

## One real title's subtitles

`title_subtitles.py` runs a real film through the subtitle pipeline (the real
speech recogniser, page checkers and manager on the configured models) in a new
isolated state directory. Give it a human track to verify (`--track`), or none
for Sparrow to write one (`--reference` compares the result with a human track).
Speech evidence is built once and reused with `--evidence-from`, so two model
setups differ only in their models. The online archive is off unless
`--archive`. It reports the outcome, the track's timing and review, and model
calls and cost by ledger phase.

```sh
SPARROW_FFMPEG=... SPARROW_FFPROBE=... SPARROW_TRANSCRIPTION_MODEL=.../whisper-medium ZAI_API_KEY=... \
  SPARROW_SUBTITLE_MODEL=glm-5.3 SPARROW_SUBTITLE_CONTRACTOR=glm-5.3-flash SPARROW_SUBTITLE_VERIFIER=glm-5.3 \
  .venv/bin/python tests/evals/title_subtitles.py --video "Four Lions.mp4" --title "Four Lions" --year 2010 \
  --track four-lions.en.srt --state data/bench/fl-verify --output fl-verify.json
```

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
(`ZAI_API_KEY`, `TMDB_API_KEY`; models from
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

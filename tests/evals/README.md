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

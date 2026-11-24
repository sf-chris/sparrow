# GLM 5.3 against the current setup, 25 November 2026

The owner asked to replace the Assistant and AltProvider models with Z.ai's GLM 5.3 and
GLM 5.3 Flash, tested on *Four Lions* subtitles and against the current setup.

| Role | Current (live) | GLM |
|---|---|---|
| Discovery, Fetch first pick, Media, Librarian | assistant-small | glm-5.3-flash (low) |
| Pick review | assistant-medium (low) | glm-5.3 (low) |
| Fetch and Media escalation | assistant-medium | glm-5.3 (high) |
| Subtitle page checker | cheap-model | glm-5.3-flash (low) |
| Second opinion | smart-model | glm-5.3 (low) |
| Subtitle manager | smart-model (medium) | glm-5.3 (high) |
| Picture subtitles | cheap-model | glm-5.3-flash |

Same code, fixtures, titles and speech evidence for both; only the models differ.

## Results

| Benchmark | Current | GLM |
|---|---|---|
| Role evaluation, 42 cases × 3 (`run_live.py`) | 121/126 · $2.28 | 121/126 · $0.46 |
| …Fetch, with the guardrail below | 24/24 · $0.60 | 24/24 · $0.08 |
| …Subtitles, after the fix below | 21/21 · $0.29 | 21/21 · $0.22 |
| Live search, 28 titles, nothing downloaded (`search_bench.py`) | 28 chosen · $1.63 · median 3.8¢ (target 2¢ missed) · 7% escalated | 28 chosen · $0.12 · median 0.4¢ · 0% escalated |
| *Four Lions*: verify the YIFY English track (2 runs) | approved, unedited, −0.175 s · $0.17–0.18 · 6 min | approved, unedited, −0.175 s · $0.21–0.22 · 12 min |
| *Four Lions*: write a track from the dialogue | approved · $0.07 · 2 min | approved · $0.14 · 8 min |

Role evaluation by role (first full run):

| Role | Current pass · cost · median s | GLM pass · cost · median s |
|---|---|---|
| Discovery | 16/18 · $0.153 · 11 | 18/18 · $0.011 · 18 |
| Fetch | 24/24 · $0.541 · 19 | 21/24 · $0.111 · 35 (24/24 with the guardrail) |
| Media | 21/24 · $1.08 · 22 | 23/24 · $0.102 · 44 |
| Librarian | 39/39 · $0.211 · 12 | 38/39 · $0.016 · 20 |
| Subtitles | 21/21 · $0.288 · 24 | 21/21 · $0.225 · 27 |

- Current failures: Discovery proposed nothing or an uninspected title (2);
  Small recorded a fake file on holdout-media-02 in all three runs, at up to
  $1.46 a run.
- GLM failures: Flash removed a finished download it was asked to mark done
  (fetch-02, 3 runs); one out-of-scope Librarian request (refused by the tool);
  one attempt to record a rejected file (refused).
- Search: the same release on 22 of 28 titles. Of the six differences GLM kept
  original audio where Assistant took an English dub (Mob Psycho 100), and took
  720p where Assistant found 1080p (Samurai Champloo); the rest are equivalent.
- Your live *Four Lions* request on the current models cost $0.66 (escalated
  Medium search $0.25, file checks $0.30).
- *Four Lions* written track against the human YIFY track: 96.6–96.9% of its
  captions overlapped, word recall 0.85–0.86 for both setups. With
  `SPARROW_SUBTITLE_MODEL=glm-5.3-flash` the manager cost $0.007 (track $0.04)
  with the same scores; it was not run on the role suite, so 5.3 stays manager.
- GLM is slower per step (1.5–2× median latency). The cheap checker on the
  verified track: Flash flagged 0 captions wrong, Cheap 120–128 (Smart cleared them).

## Found and fixed on the way

1. **Finished downloads were removable before the Media Agent checked them.**
   `client_remove` now refuses a finished download until its Media session has
   closed or a day has passed, on any model.
2. **A line heard twice broke written subtitles' citations.** Whisper reported
   "Yeah." twice at 64.2 s; GLM translated both, the published file kept one,
   and citations assigned by position shifted for every later caption. The
   approval gate refused them 30 at a time, so the GLM manager spent 67 calls
   ($1.24) realigning, and two other settings ran out of turns. Identical
   simultaneous captions now merge, and citations match by time and words.
3. The dry-run review fixture lacked the `concern` field; GLM's local schema
   check found it (AltProvider and Provider enforce schemas server-side).

## GLM facts that shaped the code

Chat Completions only (`api.z.ai/api/paas/v4`). GLM 5.3 always
thinks; `reasoning_effort` takes low, high or max and defaults to **max**, so
Sparrow always sends one. No schema mode: JSON mode plus a local schema check
with one retry. Only the flash family reads images. List prices per million
tokens: 5.3 $1.40 / $0.26 cached / $4.40 out; Flash $0.15 / $0.03 / $0.50.

Spend: GLM $4.07 (of it $3.18 on *Four Lions* incl. the diagnosis runs),
current setup $5.29. Results, logs and states: `data/glm-bench/` (not in git).

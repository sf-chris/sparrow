# Four Lions: real acquisition, speech and subtitle trial

Updated 30 September 2026. **Four Lions passes the owner's clarified watchability standard.**
This follows the [English feasibility trial](SUBTITLE_TRIAL.md) and
[subtitle plan](../SUBTITLE_PLAN.md). No production readiness flag was changed.

## Current decision: green light for watching

The owner clarified the target after this trial: confirm that the track works,
belongs to the actual movie, and follows the voice well enough to watch. Minor
translation prose differences and a few seconds of uncertainty are acceptable.
That supersedes the strict wording-completeness gate used earlier below.

Reassessed the existing evidence against that standard; no new recognition or
translation edits were needed:

- **Working track:** the exact current draft rendered through Sparrow's actual
  player at 24 checked positions, including seeking and advancing playback,
  without page or media-request errors.
- **Correct movie:** 297 complete-caption phrase matches distributed through
  the soundtrack, plus the scoped multilingual scene reviews, support the
  track's correspondence to this copy of Four Lions.
- **Usable timing:** distributed matches and focused outlier checks show no
  established sustained offset or progressive drift. A few noisy ASR estimates
  do not establish a track-level sync failure.

**Decision: accepted for watchability** for draft
`98a1ce4e70ec27656b170222f9addcedd9d7f54e09e8d726492bdd94df3738c0`.
The unresolved short uncle exchange is retained as a nonblocking wording note;
it is not an established timing fault. This is an evidence-based sampled
watchability decision, not a claim of perfect translation or direct listening.
The original strict assessment is preserved separately; no installed library
or production readiness record was changed. New titles can now be tested.

The production and experimental reviewer instructions now prioritise rendering,
identity and material timing problems, preserve good captions, and treat minor
wording/recognition caveats as nonblocking. Existing measured guardrails remain.

## Second iteration: evidence and earlier strict assessment

The owner asked to keep iterating on Four Lions before moving to another test
list. This iteration added an opt-in scene investigation on Sparrow's existing
persistent agent runtime. The agent can inspect audio observations, request a
different recogniser or crop, edit a separate full-track draft, and recheck it.
It reuses the existing Claude CLI login with execution tools disabled. Original
media, downloaded subtitles, observations and every draft revision are retained.
This is an experimental harness, not a deployed default subtitle service.

There are seven reviewed scene scopes: arrival, uncle, prayers, drone, phone,
camp orders and brother. Operator corrections were necessary; this was not an
unattended acceptance run. The current 1,638-cue draft includes these repairs:

| Film position | Change and evidence |
| --- | --- |
| 10:22 | Extend the uncle's long English caption until 10:25, before the next cue, instead of removing it during the utterance. |
| 14:25.791–14:27.332 | Replace the added reference to Mecca with a prayer-direction instruction, supported by source Urdu recognition. |
| 42:21.350–42:23.850 | Split an invitation to sit and the other speaker's refusal into separate captions with separate intervals. The downloaded caption had assigned a different request to this exchange. |
| 42:25.900–42:27.260 | Replace an assertion about being in a room with a woman with the actual question asking whether she will leave. Source Urdu preserves the request and speaker intent. |

The core camp-order passage has useful original Arabic recognition and bilingual
correspondences. However, an agent's `aligned` assessment is only a scoped model
judgement. Every experimental result still carries `verified: false`. The
whole-film acceptance gate is explicitly separate.

### What still prevents acceptance

- At approximately **11:12–11:19**, recognisers do not reliably recover two
  short questions/remarks. Narrow Arabic, Urdu, Punjabi, Hindi, English and
  automatic-language attempts produce conflicting wording. Repeated phonetics
  suggest an Arabic switch despite the screenplay draft's Punjabi label, but
  that remains an inference. Neither a screenplay nor the following English
  explanation establishes the actual source words. Original-audio clips are
  saved for a stronger independent audio review.
- In the prayer-direction exchange around **14:22**, an Arabic-forced
  “transcribe” pass appears to translate Urdu into Arabic. Its Arabic word
  timestamps were initially accepted as original speech. The operator withdrew
  that timing approval and preserved the erroneous assessment for audit. True
  Urdu recognition supports the meaning; its first-word interval includes
  leading silence. Independent Silero activity starts at 861.856 s across three
  thresholds, close to the caption's 861.875 s onset. Activity alone is not a
  word correspondence. A final focused recheck used that independently selected
  onset to request a short Urdu crop: it recovered the complete direction phrase
  from 861.75–863.09 s. Together these observations support the existing caption
  without retiming it. The first-word start is still a crop boundary, not an
  exact phoneme measurement. The corrected scene assessment replaces the
  invalid Arabic timing anchor and preserves both earlier assessments.
- The brother review initially accepted a request and an assertion as the same
  general objection, and initially placed a reply over the earlier invitation.
  Both errors were caught and repaired. Prompts now require preserving speaker,
  request/assertion, negation and separate utterances. This is a reproduced
  false-acceptance problem, not proof that a prompt change eliminates it.
- Whole-scene assessments can still overgroup phrases or leave dialogue
  supported only in prose. The experimental timing bounds do not constitute a
  calibrated human-quality standard. Production promotion needs stronger
  utterance-level coverage and independently adjudicated positive cases.

### Whole soundtrack and controlled checks

A caption-independent base-model pass covered the complete 6,066.923-second
soundtrack in **111 overlapping windows**. It took 1,741.9 seconds and peaked at
548 MiB of Python-process RSS. It also hallucinated languages and text; full
window coverage does not prove complete foreign-dialogue discovery.

The timing inventory found **297 exact complete-caption phrase matches** out of
1,638 cues. The other **1,341 remain unmatched by that lexical check**, not proven
wrong or approved. Ten-minute-bin median onset deltas were approximately
0.20–0.52 seconds. Twenty-one deltas over one second received focused medium-model
rechecks; most involved repeated phrases, omitted speech or leading silence.
Independent English CTC helped identify false first-word boundaries in two
outliers. No blanket offset was applied to satisfy model timestamps.

Two exploratory control rounds used the same movie audio with accurate-looking,
four-seconds-late and deliberately wrong-day captions. The agent detected and
repaired the wrong day, and the first round repaired the late passage. The
second late case repeatedly timed out, with unknown usage retained. Both rounds
also edited their baseline wording. In the second round multiple recognisers
support a different English conjunction from the proposed baseline; there is no
independent listening reference resolving it. These are useful reproductions,
**not a clean positive-control pass, a frozen benchmark or a quality percentage**.

### CPU observations

These runs use different audio selections and configurations and are not a
controlled model ranking. The host remains an i5-1240P with 16 GB RAM and no
NVIDIA GPU. Optional environments and weights stay in private trial storage;
production dependencies were not expanded.

| Profile | Completed scope | Wall time | Peak process RSS |
| --- | --- | ---: | ---: |
| Whisper large-v3, CPU int8 | 12 observations | 639.1 s | 5,887 MiB |
| Qwen3-ASR 0.6B, CPU float32 | 5 observations | 99.6 s | 6,004 MiB |
| Whisper medium, short language retries | 12 observations | 225.0 s | 3,200 MiB |
| English Wav2Vec2 CTC | 8 observations | 18.7 s | 1,513 MiB |
| Qwen3-ASR 1.7B, CPU bfloat16 | 4 short observations | 82.7 s | 4,864 MiB |

The earlier Qwen 1.7B float32 run was killed for memory exhaustion while other
speech work was running; a dynamic-int8 loading experiment also failed. The
bounded bfloat16 run completed but did not resolve the difficult uncle speech.
The Punjabi CTC profile emitted special tokens inside words and was rejected as
useful evidence. The worker now flags such outputs and makes their timings
unusable. Qwen recognition supplies clip-level text here, not Arabic word timing.

Primary model sources: [Whisper large-v3](https://huggingface.co/Systran/faster-whisper-large-v3),
[Qwen 0.6B](https://huggingface.co/Qwen/Qwen3-ASR-0.6B),
[Qwen 1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B),
[English CTC](https://huggingface.co/facebook/wav2vec2-base-960h) and
[Punjabi CTC](https://huggingface.co/Harveenchadha/vakyansh-wav2vec2-punjabi-pam-10).
Measured CPU results above are local observations, not model-card performance.

### Actual Sparrow playback and code checks

A private fixture ran Sparrow's actual backend, authentication, catalogue,
playback routes and built frontend against a stream-copied full movie containing
the draft track. It did not alter the installed owner's account or library.
Chrome checked **24 playback positions**, active cue text, decoded dimensions,
seek clocks and advancing playback, with no page or media-request errors.
Overlapping cues were represented as simultaneous text over their original
display intervals. The tested draft revision is recorded in `player-copy.json`
and `player-check/result.json`.

This exposed a real player bug: a paused seek left the buffering badge over the
captions. `Watch.tsx` now clears that state on `canplay`; the browser recheck
asserts the badge is absent. The operator inspected the rendered corrected
captions. The automated browser was muted; this proves playback/rendering, not
that the assistant listened or that a bilingual person approved the soundtrack.

**32 subtitle/provider/trial tests pass**, including source identity, immutable
drafts, stale-write rejection, patch receipt recovery, timing-limit rejection,
preserving interior untimed negations, contiguous compact word ranges, CLI
isolation and existing production subtitle/playback behaviour. The frontend
production build passes, with its existing HLS chunk-size warning. These checks
do not establish speech accuracy.

Current private evidence is under `data/four-lions-trial/iteration-2/`:

- `case/`: source bindings, hash-addressed observations, full-track revisions,
  scene assessments and durable sessions; prior operator-corrected approvals
  remain beside the current assessments.
- `timeline/`, `timeline-audit.json`, `outliers/`, `outlier-recheck.json`, `ctc/`:
  full-window observations and focused timing checks.
- `controls/`, `controls-v2/`: separate synthetic-caption control cases and
  their complete repair histories, including failures.
- `qwen-bf16/`: completed lower-memory 1.7B run and resource measurements.
- `player-check/`: actual Sparrow screenshots, cue/clock assertions and errors.
- `remaining-review/`: three short original-audio clips and an audio-first
  review request, without candidate text supplied to recognition.

CLI receipts retain reported list-price estimates and unknown interrupted usage;
neither is a subscription invoice. Repeated failed reservations were not erased
to bypass allowances. Original source files and all failed experiments remain.

The next gate is an independent audio-capable review of the unresolved clips,
then rechecking any repairs and the exact final revision in Sparrow. The current
local recognisers plus text-only Claude have not met the owner's completion
standard; do not move this title to accepted merely because code tests pass.
At the end of this iteration, six scene assessments are aligned against their
current cue hashes; the uncle scene remains `needs_evidence`. A separate bounded
review of its three short cues also refused acceptance. The superseded larger
uncle session was interrupted after this focused result; its cancellation
receipt and unknown usage remain recorded. `acceptance.json` records the final
draft, scopes, controls and playback evidence with whole-film acceptance false.

## First iteration — 29 September 2026

## Actual acquisition and evidence

At the owner's request, used Sparrow's existing search service to find the film,
then its node executor and Transmission integration to download the complete
file and probe it. The installed instance had no configured downloader, library
or Anthropic API key, so this used private trial storage and an isolated local
downloader. The installed owner's configuration and accounts were not changed.
The downloader was stopped after completion; the media and receipts remain.

This exercised real search, node acquisition, download completion and probing.
It was **not** an autonomous Fetch/Media job through the installed UI. The first
node request omitted its expiry and was correctly refused; a new, complete
request succeeded. Both receipts were preserved.

The actual file is 1,753,979,629 bytes, 6066.923 seconds, with one stereo audio
stream and no embedded subtitle stream. Source SHA-256:
`78b994b0f9b99db405c0bb0e6fc8d6591e9531223324b6e266609ac44745aa72`.
English audio metadata did not identify the language switches. The film includes
Arabic, English, Punjabi and Urdu according to its
[official festival listing](https://www1.nziff.co.nz/2010/archive/four-lions/).

Downloaded and preserved three external candidates:

- The first was malformed: its timeline restarted halfway through. Sparrow's
  parser rejected it. Sorting a diagnostic copy did not make it an accepted track.
- An ordinary English candidate from [SUBDL](https://subdl.com/en/subtitle/sd4493/four-lions)
  contains no caption covering the Arabic utterance at 20:24.46–20:29.50.
- A second candidate includes translations of foreign dialogue, including that
  passage. Its translation remains a candidate, not reference truth.

Inspected eight actual video frames at 14:08, 14:14, 14:23, 14:44, 14:52,
19:54, 20:11 and 20:25. None showed burned-in subtitles. This establishes only
those frames; it is not a whole-film burned-caption audit.

## Recognition from the actual soundtrack

Decoded the film's selected audio onto the same playback clock as the video.
Recognition received audio only, without subtitle text or a translation prompt.
The base-model discovery pass covered every window from 11:00 to 21:00,
including intervals without captions. Then sampled eleven scenes, with narrower
language-hypothesis retries, and tried a third recogniser configuration.

Host: Intel i5-1240P, 16 GB RAM, CPU int8 inference, no NVIDIA GPU. These are
exploratory runs with different chunk sizes, decoding options and concurrency;
they are not a controlled speed ranking or full-film performance estimate.

| Run | Scope | Wall time | Peak Python-process RSS | Result |
| --- | --- | ---: | ---: | --- |
| Bundled Whisper base, automatic language, 2 threads | 10 minutes; 30 overlapping windows | 388.24 s, including extraction/hashing | Not measured on this movie run | False language choices and unreliable foreign dialogue |
| Whisper medium, automatic language, 4 threads | 11 selected scenes | 311.59 s | 3306.5 MiB | Some useful Arabic; skips foreign speech in mixed-language clips |
| Whisper medium, language hypotheses, 2 threads | 11 narrower clips | 121.73 s | 2107.2 MiB | Better passages, but incomplete or implausible wording persists |
| Whisper large-v3-turbo, 2 threads | 6 scenes, automatic and hinted passes | 184.96 s | 2025.1 MiB | Still omits speech or repeats invented content |

Models: [medium](https://huggingface.co/Systran/faster-whisper-medium) and
[Turbo conversion](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo).
All are Whisper-family models; agreement is not independent bilingual proof.
Model hashes, options, word confidences, timings and every saved observation are
retained. An initial Turbo harness run failed serialising a NumPy boolean; the
recorded completed run is `turbo-scenes-v2`, using explicit native scalar types.

Specific failures matter more than mean confidence:

- Around 08:06, automatic recognition skips a brief foreign-language question
  between English lines. A forced language over the larger mixed-language clip
  instead produces repetitive nonsense.
- Around 14:06 and 14:50, automatic recognition emits repetitive English rather
  than a useful original-language transcript. Arabic hypotheses improve parts
  of the drone passage but do not establish complete wording.
- Around 20:24, medium recognises useful Arabic. Turbo still fails this passage.
  A narrow medium crop moves the first word's estimated onset from 1224.46 to
  the crop boundary, 1223.70: a 0.76-second difference. Precise-looking word
  timestamps therefore need independent acoustic validation.
- Some repetitive failures have high model confidence. A confidence threshold
  alone cannot prevent false acceptance.

## Real Claude comparison, using the existing sign-in

The owner's existing Claude CLI authentication worked without a new account or
API key. The opt-in [CLI adapter](../../backend/agents/subtitle_cli_trial.py)
disables CLI execution tools, MCP integrations and local customisations. It
returns proposed actions to Sparrow's actual persistent tool loop; Sparrow
executes inspection and submission, records receipts and enforces evidence IDs.
Claude receives source-language transcript/caption text, **not audio or video**.
This Linux trial adapter does not replace the production Anthropic API provider.

Ran Haiku 4.5 on the translated passage, then Sonnet 4.6 on the unchanged
candidate and three negative cases. Source audio and ASR evidence were identical
across the cases. Modified captions were separate derivatives, never originals.

| Case | Observed result |
| --- | --- |
| Ordinary English track | No cues in the scoped passage; all 12 recognised word observations remain unmapped. Claude explicitly leaves the work incomplete. The complete source subtitle also has no caption at the utterance's onset. |
| Downloaded foreign-dialogue translation | Sonnet groups reordered clauses but retains uncertainty about an extra title in the caption and an ambiguous instruction in the ASR. No quality approval. |
| Captions deliberately shifted +4 seconds | Tools measure onset deltas of +3.665 and +4.198 seconds for the two phrase groups; offsets differ from the unchanged case by exactly +4 seconds. Sonnet identifies systematic lateness. |
| One word changed from “Tomorrow” to “Yesterday” | Sonnet identifies the semantic inversion from the recognised Arabic بكرة, despite unchanged timing. |

A concrete correspondence: medium places **بكرة** at **20:24.460–20:25.100**;
the downloaded English caption containing **“Tomorrow”** begins at
**20:24.125**, 0.335 seconds before the estimated spoken onset. This is an
audio-derived observation plus a bilingual text judgement, not independently
certified word timing or a claim that the assistant heard the audio.

Both models initially tried noncontiguous word mappings; Sparrow rejected them.
Haiku also criticised ordinary clause reordering and treated an ambiguous Arabic
verb as a confident equivalent. Sonnet handled that ambiguity more cautiously.
After clarifying the grouping instruction, Sonnet submitted contiguous phrase
groups. These were exploratory corrections, not a frozen or repeated benchmark.
The unchanged and delayed cases also differed in semantic certainty despite
identical wording: timing and meaning still need better separation in evaluation.

The two longer Sonnet cases initially stopped at the $0.50 estimated runtime
allowance because of retry reservations. Their saved sessions resumed with a
$1 experimental allowance and the grouping clarification; previous calls were
not replayed. All five diagnostic sessions finished with `verified: false`.
Across 19 CLI calls, CLI-reported list-price estimates total **$0.893436**;
the authentication smoke test separately reported $0.000682. These are not
subscription invoices. CLI internal formatting/retry usage does not exactly
match Sparrow's API-style token cost estimate; receipts preserve both. This
adapter needs accounting work before production consideration.

## Actual caption rendering

Created private video excerpts at 14:04–15:04 and 20:18–20:32 with their original
soundtrack and derived WebVTT candidates. Chrome 127 decoded them, sought to
the requested times, advanced playback, and rendered the native subtitle track.
Inspected screenshots of the rendered camp-order and drone captions.

At film time 20:24.5, the translation is visible; the ordinary and +4-second
tracks have no active cue. At 20:28.5, the delayed version displays its first
translated cue. The drone caption is visible at 14:52. Saved browser observations
include exact currentTime, active cues, decoded dimensions and absence of media
or page errors. An initial simple HTTP server did not support reliable seeking;
the successful check used Starlette's range-capable file serving and asserted
the resulting clock, not merely completion of a seek event.

This was a separate diagnostic page with a muted automated browser, **not an
independent listening check or acceptance in Sparrow's installed player**. The
assistant inspected still frames and recognised text; it did not listen to the
whole film or establish a bilingual reference. Temporary servers were stopped.

## Reproduction and remaining gate

Private local artifacts are under `data/four-lions-trial/` (ignored by Git):

- `sparrow-search.json`, acquisition requests/receipts, `download-complete.json`
  and `sparrow-probe.json`: acquisition facts and complete source location.
- `subtitles/`: original downloaded archives/text, decoded candidates and URLs.
- `base-discovery/`: full extracted soundtrack, source identity and 30 windows.
- `medium-scenes/`, `medium-focused/`, `turbo-scenes-v2/`: actual speech results.
- `review-translation/`, `sonnet-{translation,late,wrong-meaning,missing}/`:
  immutable source bindings, private request/result receipts, durable agent
  sessions and measured comparisons.
- `playback/`: two playable excerpts, candidate tracks, diagnostic HTML,
  screenshots and `browser-result.json`.

For a new excerpt, use a fresh output directory:

```sh
export SPARROW_FFMPEG="$PWD/data/subtitle-trial-tools/bin/ffmpeg"
export SPARROW_FFPROBE="$PWD/data/subtitle-trial-tools/bin/ffprobe"
.venv/bin/python -m backend.agents.subtitle_trial \
  --media '/path/to/the/downloaded/movie.mp4' --audio-index 1 \
  --model-path data/four-lions-trial/models/whisper-medium \
  --subtitles data/four-lions-trial/subtitles/with-foreign-translation-0.utf8.srt \
  --start 1220 --seconds 10 --output data/four-lions-new-trial
.venv/bin/python -m backend.agents.subtitle_trial \
  --output data/four-lions-new-trial --review --review-only --claude-cli \
  --reasoning-model claude-sonnet-4-6
```

The general CLI retains the full subtitle candidate; the live comparisons above
used explicitly scoped excerpts. A whole-candidate review may exhaust its
allowance. No result publishes or repairs captions automatically.

Validation: **21 subtitle/provider/trial tests passed**, including CLI tool
isolation, saved receipts before returning actions, rejection of unregistered
actions, cancellation with unknown-usage preservation, evidence integrity and
real FFmpeg timestamp handling. Compilation, dependency consistency and diff
whitespace checks passed. These code checks do not establish movie speech quality.

The trial demonstrates real audio extraction, some useful cross-language
correspondences, semantic negative detection, measured timing and rendering.
It also supplies real failures that prevent calling this feature finished.
Next gates are reliable language-switch coverage, independent source-word and
acoustic validation, stable bilingual judgements, a complete foreign-dialogue
inventory, justified repairs and final playback in Sparrow. None can be replaced
by model confidence, agreement within the Whisper family or a successful process.

# Audio-first subtitle feasibility trial

29 September 2026. This is the first opt-in experiment for stage 5 / issue #3 P6,
following the [subtitle plan](../SUBTITLE_PLAN.md). It does not change installed
Sparrow, publish subtitles, or satisfy multilingual/movie/player acceptance.
The subsequent [Four Lions trial](FOUR_LIONS_TRIAL.md) acquired the actual film,
sampled foreign speech and ran live Claude comparisons through the existing
CLI sign-in. The English-only results below retain their original scope.

## What ran

The installed library contained no items or paired storage nodes, and Sparrow
had no Anthropic key configured. The requested *Four Lions* copy and existing
key location were requested from the owner at this initial stage. No private
film was retrieved or watched, and no paid inference or external subtitle-provider
call occurred in that initial experiment.

Copied the already-bundled Linux FFmpeg/ffprobe and multilingual Whisper base
model into the ignored local `data/subtitle-trial-tools/` directory. The trial
used the repository's eight real LibriSpeech recordings with their existing
[attribution](../../tests/fixtures/subtitles/ATTRIBUTION.md), concatenated with
silence and muxed into a black-video MP4. Candidate captions were deliberately
shifted four seconds late. ASR received only the extracted audio, not captions.

Two successful local passes processed the whole 53.184-second decoded soundtrack
in three overlapping windows. The reproducible benchmark recorded 6.27 seconds
for extraction, model loading, hashing and transcription, and 359.8 MiB peak
Python-process RSS. This excludes separate FFmpeg subprocess peak memory; it is
a short English/base-model measurement, not an Arabic-film throughput claim.
Configuration: CPU int8, two inference threads, beam size five, word timestamps,
independent language detection per window, no VAD filtering or caption prompt.

The assistant inspected the saved transcript and associated its phrases with
the eight candidate captions. The resulting measured onset differences ranged
from 3.54 to 4.32 seconds late, with a median of 3.91 seconds, against the injected
4-second shift. These correspondences were selected by Codex in this session;
they are **not a live Claude API result or an autonomous agent evaluation**.
Fixture caption boundaries describe recording clips, not independently labelled
word onsets, so the residuals do not establish word-level accuracy.

Two concrete problems remain visible:

- ASR wrote “Concorde” where the reference says “concord”; that correspondence
  retained uncertainty instead of treating precise timestamps as correct wording.
- The onset of “It” in the same short utterance differed by 0.88 seconds across
  overlapping windows. A zero-duration punctuation token was also retained and
  marked unusable for timing. No observation was silently discarded.

The experiment demonstrates inexpensive local transcription and grounded timing
measurement. It also demonstrates why this alone cannot certify perfect sync.

## Implemented trial boundary

[subtitle_audio.py](../../backend/agents/subtitle_audio.py) extracts an explicit
audio stream, retains container/audio start differences on the playback clock,
records source/model/artifact hashes, and saves each caption-independent window,
word, confidence and timing. Multiple audio tracks require an explicit choice.
`--seconds` limits recognition to a labelled excerpt; extraction currently decodes
the whole soundtrack first. No input media or caption file is overwritten.

[subtitle_trial.py](../../backend/agents/subtitle_trial.py) optionally uses the
existing persistent agent runtime for a Claude inspection/submission tool loop.
It retains invocation receipts, complete observations through the evidence
archive, bounded retrieval, an eight-turn limit and a $0.50 estimated allowance
with the runtime's retry reservations. Interrupted reasoning can continue from
the same saved session. Changed input hashes prevent reusing an old review.

The model proposes phrase/cue IDs and semantic opinions. Tools derive times from
those IDs and reject invented timestamps/words, skipped intervening words,
duplicate cue assignments and unusable word times. Reports retain unassessed
cues and unmapped word observations; overlapping windows include duplicates.
Every result carries `verified: false`. Completing a diagnostic session does
not mean completing subtitle verification. There is no publication tool.

The Claude path was tested with controlled responses only. The local run with
`--review` retained its transcript and reported `awaiting_api_key`, exit code 2.
Normal transcription-only runs exit zero; they explicitly disclaim semantic,
independent-listening and playback verification. ASR interruption retains partial
artifacts but currently requires a fresh output directory to rerun recognition.

## Reproduce

From the repository root, using the copied installed components:

```sh
export SPARROW_FFMPEG="$PWD/data/subtitle-trial-tools/bin/ffmpeg"
export SPARROW_FFPROBE="$PWD/data/subtitle-trial-tools/bin/ffprobe"
export SPARROW_TRANSCRIPTION_MODEL="$PWD/data/subtitle-trial-tools/whisper-base"
.venv/bin/python tests/subtitle_audio_benchmark.py data/subtitle-trial-new-run
```

For actual media, choose the intended stream and an existing caption candidate:

```sh
.venv/bin/python -m backend.agents.subtitle_trial \
  --media /path/to/movie.mkv --audio-index 1 \
  --subtitles /path/to/movie.en.srt \
  --start 0 --seconds 120 --output data/subtitle-movie-trial
```

Omit `--seconds` to transcribe the entire soundtrack, bounded to six hours.
Once `ANTHROPIC_API_KEY` is supplied through the process environment, continue:

```sh
.venv/bin/python -m backend.agents.subtitle_trial \
  --output data/subtitle-movie-trial --review --review-only
```

The optional review sends recognised text and subtitle text to Anthropic. It
does not upload raw audio/video. Default reasoning model follows
`SPARROW_CHEAP_MODEL`, otherwise `claude-haiku-4-5`; this is not a model-selection
result. Use `--reasoning-model` for an explicit experimental choice. The review
can remain incomplete if its bounded allowance cannot cover the selected input.

Local artifacts for this session:

- `data/subtitle-audio-benchmark-20260929/results.json`: reproducible timing/RSS.
- `data/subtitle-audio-benchmark-20260929/trial/`: complete recorded observations.
- `data/subtitle-trial-english-v2/assistant-comparison.json`: assistant-selected
  phrase correspondences and measured timing differences from the first pass.

Validation: all **18** subtitle/provider/trial tests passed, including nine new
trial tests. They cover nonzero container timestamps with delayed audio using
real FFmpeg, preservation, invalid evidence, skipped words, changed-input review
rejection, durable controlled tool-loop resumption and budget exhaustion. Python
compilation and dependency consistency passed. No browser or live bilingual
acceptance is claimed.

The later [movie trial](FOUR_LIONS_TRIAL.md) resolves acquisition and the existing
Claude sign-in. Source-language accuracy, independent listening, justified repairs
and installed-player acceptance remain open; that record details actual findings.

# Subtitle evidence, timing correction and review — 30 September 2026

The owner's current subtitle target is foreign-language audio with English
captions. A reviewer should be able to act like a bilingual viewer: see what was
said, when the voice starts, and which caption is on screen. Phase 1 builds the
caption-independent evidence and the timing measurement; phases 2 and 3 put it
into the product with page-by-page reviewer tools and an Opus 5.5 reviewer
([below](#in-the-product)).

## What was built

| Component | Behaviour |
| --- | --- |
| `subtitle_evidence.py` | Decodes the chosen stream once on the container playback clock (delayed audio and non-zero starts preserved), maps speech probability in 32 ms frames across the whole timeline, transcribes every second in contiguous ≤28 s chunks cut at the quietest moments, splits recognised segments where the detector hears a pause, and saves utterances with stable IDs, words, acoustic onsets and flags. Checkpointed per chunk; time-sliced; niced. Subtitle text is never an input. |
| `subtitle_node.py` `subtitle_evidence` | Node operation with a time budget per call, completed-evidence reuse keyed by media version, audio stream, model and options, medium-first model choice with base fallback, 2–4 threads (a quarter of the machine). |
| `subtitle_sync.py` | Offset, confidence interval, drift (including 23.976/24/25 fps ratios), three-minute section profile, per-line outliers and a consistency test that refuses to correct unrelated tracks. Proposes the least invasive correction: none, constant shift or linear drift. |
| `tests/subtitle_sync_calibration.py` | Absolute onset calibration on licensed English clips placed at known positions (clean and with background noise) plus professional-track baselines. |

The audio label chooses the recognition language. Only doubtful chunks (low word
confidence or log-probability) run language detection, which costs another
encoder pass; a confident different language (≥0.8) is transcribed in that
language and flagged. Flags are informational and never delete text.

## Measured findings

- **The speech detector cannot decide what gets transcribed.** On Texhnolyze
  S1E5, detector-gated chunks left 13 of 127 captioned lines untranscribed; with
  whole-timeline chunks every caption is inside transcribed audio. Continuous
  background music at about −30 dBFS suppresses the detector on roughly a fifth
  of lines, including clearly spoken dialogue and the ending song, which Whisper
  still recognises correctly. A stereo centre upmix did not help (102 vs 104
  lines detected). The flag is therefore `speech_detector_silent`, not a
  hallucination signal.
- **Detector onsets are consistently late by about 90 ms.** Across 51 English
  clip onsets the median lag was 93 ms (clean), 84 ms (−45 dBFS noise) and
  95 ms (−35 dBFS noise). Whisper word starts were about 150–190 ms *early* with
  a 90th-percentile error near 0.56 s, so they are recorded as approximate and
  excluded from offset measurement. The reference onset is the rising edge of
  the first voiced syllable; room tone and breaths 25–40 dB below the voice were
  the cause of two earlier, rejected reference definitions.
- **Human-timed tracks differ, and each is consistent.** After the 90 ms
  correction, and with no line further than 250 ms from its track's fit:

  | Track | Caption start vs voice | 95% CI | Pairs | Every 3-minute section |
  | --- | ---: | ---: | ---: | --- |
  | Texhnolyze S1E5 | −32 ms | ±25 ms | 74 | −83 to +31 ms |
  | Haibane Renmei S1E8 | **+220 ms** | ±14 ms | 67 | +194 to +245 ms |
  | House of Five Leaves S1E11 | −189 ms | ±13 ms | 172 | −219 to −174 ms |

  Recogniser word starts, an independent clock, reproduce the differences
  between tracks within about 20 ms. Haibane's captions are late throughout;
  the earlier sampled trial accepted it under its 0.75 s tolerance. Five Leaves
  uses a deliberate lead-in.
- **Drift and frame-rate mismatches are corrected; unrelated tracks are not.**
  On real Texhnolyze evidence, ×1.001, ÷1.001, 25/23.976, 23.976/25 with a
  2 s offset, and a free ×1.0005 drift were each detected and corrected back to
  within about 0.1 s of the original track across the episode. Haibane and
  House of Five Leaves captions against Texhnolyze were inconsistent
  (prominence 1.22/1.29, 16–27% onset match versus 3.9 and 80% for the real
  track) and no correction was proposed.
- **Turbo is the preferred model.** On the whole of Texhnolyze S1E5 (4 threads,
  shared host, so wall times are upper bounds):

  | Model | Transcription | Peak memory | Sharp onsets | Examples |
  | --- | ---: | ---: | ---: | --- |
  | base | 6.4 min | 0.6 GB | 68 | 特徴席 for 特等席, 手伝おき的 for 哲学的 |
  | medium | 40 min (heavier contention) | 3.9 GB | 73 | より書かれない for 寄りかかれない, しばゆー for 芝居 |
  | large-v3-turbo | 15 min | 2.7 GB | 89 | correct in each case above |

  Both larger models invent text over silence or music (medium "あ…" ×4, turbo
  "ご視聴ありがとうございました"); known phantom phrases are flagged.

## Heuristics chosen

| Setting | Value | Reason |
| --- | --- | --- |
| Transcription coverage | Whole timeline | Detector misses ~20% of dialogue under music |
| Onset source for timing | Detector, clean onsets only (≥0.3 s quiet before) | Sharp, consistent bias; recogniser starts are noisy |
| Onset correction | −90 ms (`ONSET_BIAS`) | Calibration above |
| Target caption position | At the voice (0 ms) | Owner wants captions with the voice |
| Correction window | More than 50 ms late, or more than 200 ms early; only when the 95% CI is tighter than half the error | Late captions feel laggy; short lead-ins are a common deliberate convention. Haibane is shifted −220 ms (then measures 0 ms); the other two tracks are unchanged |
| Drift correction | Only if it moves captions >50 ms across the track and exceeds its CI | Noise alone produces ~0.1 s apparent drift |
| Line review tolerance | 250 ms from the fitted line | Deliberate lead-ins and shot-change timing are normal |
| Evidence model | large-v3-turbo, then medium, then base | Accuracy and speed above |
| Whisper retries | Temperatures 0/0.3/0.6, best of 3 | Default six-step fallback cost up to 7× real time on music and invented repeated text |
| Threads and priority | 2–4 threads, nice 10 | Background work must not starve playback |

## In the product

A subtitle task now runs:

1. **Find.** Local candidates are ordered by exact language then cue count (from
   Matroska statistics tags or one counting pass, never a decode per track), then
   provider results. The first that prepares becomes a playable track at once.
2. **Measure and correct**, without a model call, for foreign dialogue or when
   checking or fixing is requested. The node builds evidence in resumable
   nine-minute slices, one title at a time per node. A consistent track outside
   the window gets a corrected copy (original kept), which must re-measure inside
   it or the original is kept. An inconsistent track is set aside and the next
   candidate tried; without checking it remains as a fallback if nothing better
   is found.
3. **The subtitle agent**, when the household switches it on and an Anthropic
   key is set. Opus 5.5 (effort high, prompt caching, its own per-title
   allowance, `SPARROW_SUBTITLE_BUDGET`, default $10) owns the outcome in
   Sparrow's persistent tool loop. It reads each page's recognised speech and
   writes its own reading before that page's captions are revealed, then
   judges every caption, citing speech lines. It can `retime` the track (a
   measured or given shift, measured drift, or each section to its measured
   offset), `edit_captions` (new words, align to cited speech lines, nudge,
   remove, or add captions for uncaptioned dialogue), `use_source` or
   `search_online` for another track, `listen` again with a language hint or an
   alternative or larger recogniser, and, when no source is usable,
   `write_page` and `use_written` to write the subtitles itself, timed to the
   speech it cites. Timestamps always come from the audio. Every change is a
   new copy, re-measured; pages whose captions changed are judged again.
   Approval is refused while any page is unjudged, any caption is wrong,
   dialogue is uncaptioned (full tracks), a matched caption sits over 1.5 s from
   its speech, over a quarter of captions are unclear, or the track or any
   section is outside 50 ms late / 200 ms early of the voice. A judgement cannot
   be made in the same step that revealed the captions.
4. **Continue safely.** Review state lives in the task, so a new conversation
   resumes it rather than editing an earlier one; all conversations for a
   title share one allowance. An outage or interrupted turn resumes on the
   timer. With the agent off, steps 1–2 still fix timing without AI.

Supporting fixes: queueing subtitles can no longer fail filing a download; the
queue holds 500 tasks; stronger requests upgrade a running task; ASS events play
in time order and vector drawings are dropped; a prepared copy hides its embedded
original in the player; re-runs supersede their own older copies but never replace
a checked copy with an unchecked one; activity wording distinguishes ready from
checked. The five-sample checker, FFsubsync path, `subtitle_quality.py` and the
old benchmark were removed. FFsubsync remains a locked dependency pending removal.

Validation: 211 tests pass, including the flow on a generated video with a real
node and worker (retiming, gloss-before-captions, refused approval, next-source
fallback, missing key, cancellation) and an end-to-end run on the real Haibane
file: the 204-cue track was chosen over the 11-cue signs track, measured +217 ms,
corrected, re-measured at 0 ms and listed in place of the embedded original. Live
Opus reviews are validated separately on the owner's installation.

## Limits

Recognition can omit or mishear speech. The detector misses dialogue under
music, so a minority of lines have only approximate onsets; track and section
offsets rely on the sharp majority. Calibration uses English read speech and three
anime episodes; other mixes, languages and broadcast timing conventions need
their own checks. Evidence is keyed by file size and modification time, so a
touched file is re-analysed; old evidence folders and decoded audio (about 46 MB
per 24-minute episode, retained for phase 2 re-listening) need a cache policy.
Timings in this record were measured on a shared server with unrelated processing
workers running, so wall times are upper bounds.

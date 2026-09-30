# Anime subtitle trial — 30 September 2026

The owner requested three less-mainstream anime, one randomly selected episode
each, plus subtitle inclusion/checking controls. Acceptance is practical
watchability: working captions for the actual content with usable voice timing.
Minor translation wording differences are not a reason to rewrite a usable track.

## Fixture and scope

Isolated state and all copyrighted media/evidence remain under ignored
`data/anime-subtitle-trial/`. Only one episode from each torrent was selected:
Haibane Renmei S1E8, House of Five Leaves S1E11 and Texhnolyze S1E5. The original
Kaiba S1E7 source had no connected peers and was stopped without completing.
Selection used recorded random seeds. Sparrow search/downloader components and
an isolated Transmission daemon acquired the files; the owner-selected identities
were registered in the fixture after completion, file hashing and real probing.
This is not an autonomous Fetch/Media or configured-TMDB acceptance run.

The fixture runs the actual Sparrow app, authenticated subtitle APIs, node worker,
persistent review runtime and player. Japanese audio is explicitly selected,
including for the two copies with default English dubs. Independent speech
evidence uses local Whisper medium, CPU int8, two threads, five 30-second windows
distributed through each episode. Claude Haiku 4.5 reviews those observations;
a proposed rejection of translated captions receives a Sonnet 4.6 second review
in the final fixture. The normal configurable cheap/smart roles are preserved.
These measurements do not evaluate the smaller packaged Whisper base model.

The existing Claude CLI login supplies bounded experimental model calls. Sparrow
owns the real tool loop and durable receipts. Normal application calls still use
the existing Anthropic API integration; no new provider/account was introduced.
CLI list-price estimates are not subscription invoices.

## Defects found and fixed

- Haibane's default English track contains 11 signs/name captions. Its separate
  full track contains 204 cues. Candidate selection now inspects actual cue counts
  instead of assuming the default track contains full dialogue. Counts guide
  selection; they do not prove meaning or synchronisation.
- Japanese/English lexical comparison previously produced misleading zero-match
  scores. Translated tracks now request bilingual phrase correspondence without
  reporting cross-language text equality as a failed translation.
- Asking the model to count Japanese subword indices produced false offsets up
  to several seconds. The agent now quotes a source phrase; the tool locates its
  unique occurrence in the saved words and computes the times itself. Fabricated
  or ambiguous quotes cannot become timing evidence.
- The cheaper reviewer sometimes matched only a clause or unrelated neighbouring
  dialogue. The prompt requires complete corresponding phrases, permits missing
  recognition to remain uncertain, and requests one stronger review before a
  translated-track rejection becomes final. Neither reviewer can approve failed
  measurements or change the speech evidence.

## Product behavior

Subtitle inclusion defaults on; paid sync checking defaults off. Household,
personal and explicit request choices use the same versioned resolver. Landed
downloads and imports enqueue preparation with that effective contract. Explicit
required-subtitle requests still prepare captions when background inclusion is
off. Older request snapshots retain their previous verification requirement.

Basic preparation extracts/normalises a usable track, preserves timings and calls
neither ASR nor a model. Checking compares independent source phrases with captions.
Fixing explicitly runs an alignment candidate and, when requested, checks it.
Original tracks and personal offsets remain available. Titles, episode rows and
the player expose the actions, and only approved tracks say “sync checked”.

## Results

All three copies pass sampled watchability without rewriting or shifting their
original dialogue captions:

| Episode | English cues | Corresponding speech samples | Median absolute onset difference | Largest measured onset difference |
| --- | ---: | ---: | ---: | ---: |
| Haibane Renmei S1E8 | 204 | 4 of 5 | 0.242 s | 0.349 s |
| House of Five Leaves S1E11 | 264 | 4 of 5 | 0.205 s | 0.390 s |
| Texhnolyze S1E5 | 127 | 5 of 5 | 0.183 s | 1.116 s |

House of Five Leaves needed the stronger reviewer and retries of incomplete
reviews. A CLI call hit its private $0.35 allowance before returning a result;
the fixture then used bounded 1,024-token thinking. Existing evidence, measured
gates and original captions were retained. The eventual corrected correspondences
passed; an earlier attempt to approve failed measurements was refused by the tool.
These are iterative development cases, not clean unseen holdout evaluations.

Two real uploaded-caption controls were rejected. Adding four seconds to every
Haibane cue produced a measured median **4.288 s late** offset. Uploading House of
Five Leaves captions against Haibane produced unrelated dialogue and failed
measurements. Existing good tracks remained playable. The first shifted-control
review described the direction incorrectly as early; the numeric evidence was
correct and the prompt now explicitly defines the sign of the offset.

The private `acceptance.json` binds task/evidence snapshots, media hashes, browser
results and receipt hashes. Across all recorded development/review attempts,
36 CLI receipts report **$1.8787** in list-price estimates, including the failed
allowance-limited call. This is neither a per-episode price nor an extra invoice
for the existing CLI subscription.

The final review loop is bounded to eight calls per wake and the existing shared
spending policy. It can request five other distributed speech samples once; tools
prevent duplicate cues from counting as independent matches. That extension has
controlled runtime/worker coverage, including idempotence and preserved originals.
The three recorded passes used their original five-sample sets; they do not
establish live quality for the additional-sampling branch.

## Validation boundary

Actual Chromium playback passed eight distributed caption positions per episode
(24 total), seeks, advancing video, authenticated subtitle delivery and narrow
390-pixel layouts. Saved frames were visually inspected. Browser runs are muted:
they prove rendering/playback, not independent human listening. Local ASR processes
the soundtrack; Claude reasons over that recorded speech evidence.

Preference browser checks pass default inclusion/on and checking/off, save/reload,
and inheritance by the per-title action. A desktop browser with a narrow viewport
does not establish physical-phone or Safari support. ASS dialogue is converted to
plain WebVTT; full fansub typography/layout is not preserved by this pipeline.

**77 tests pass** across subtitle preparation/review, provider/trial investigations,
accounts, playback, node acquisition and product correctness. The frontend build
passes with its existing bundle-size warning. Tests cover default preparation with
no ASR/model call, preference inheritance/import wiring, preserved legacy required
checks, bilingual measurements, refusal of invented quotes and large offsets/drift,
review escalation, extra-sample bounds and separation of available/checked labels.

The source tree and isolated fixture are changed; the owner's installed container,
accounts and library are not deployed or modified. Live external subtitle-provider
acceptance, image subtitles and independent holdout evaluation remain separate.

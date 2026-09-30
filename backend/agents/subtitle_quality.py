"""Measure agent-selected bilingual correspondences against saved speech words.

The model judges which phrase matches. This tool supplies all times and checks
distributed coverage; the model cannot invent or replace acoustic observations.
"""

import math
from statistics import median


def measure_correspondences(evidence, correspondences):
    samples = evidence.get("samples", [])
    if evidence.get("quality", {}).get("structural_reasons"):
        raise ValueError("The track has structural problems; find another candidate.")
    if not 3 <= len(correspondences) <= len(samples):
        raise ValueError("Match at least three distinct sampled utterances.")
    measured, seen, seen_cues = [], set(), set()
    for match in correspondences:
        index = match["sample"]
        if type(index) is not int or index in seen or not 0 <= index < len(samples):
            raise ValueError("Use distinct saved samples.")
        seen.add(index)
        sample = samples[index]
        if sample["cue_index"] in seen_cues:
            raise ValueError(
                "Match distinct captions, not repeated samples of one cue."
            )
        seen_cues.add(sample["cue_index"])
        words = sample["words"]
        if "source_text" in match:
            # The agent quotes the matching phrase; code locates its timestamps.
            # Asking models to count Japanese subword indices produces false offsets.
            normalize = lambda text: "".join(c.casefold() for c in text if c.isalnum())
            target = normalize(match["source_text"])
            ranges = []
            for begin in range(len(words)):
                phrase = ""
                for finish in range(begin, len(words)):
                    phrase += normalize(words[finish]["text"])
                    if phrase == target and target:
                        ranges.append((begin, finish))
                    if len(phrase) >= len(target):
                        break
            if len(ranges) != 1:
                raise ValueError(
                    f"Sample {index}: quote a unique, verbatim source phrase from its saved words. Do not translate or correct the transcription."
                )
            first, last = ranges[0]
        else:
            # Older durable sessions may still return indexed correspondences.
            first, last = match["first_word"], match["last_word"]
        if any(type(v) is not int for v in (first, last)):
            raise ValueError("Use integer word indices from the evidence.")
        if not 0 <= first <= last < len(words):
            raise ValueError("Use a contiguous source phrase within this sample.")
        explanation = match.get("explanation", "").strip()
        if not explanation or len(explanation) > 600:
            raise ValueError(
                "Explain briefly how this source phrase matches the caption."
            )
        phrase = words[first : last + 1]
        start, end = phrase[0]["start"], phrase[-1]["end"]
        if (
            not all(math.isfinite(v) for v in (start, end))
            or not sample["start"] <= start < end <= sample["end"] + 0.05
        ):
            raise ValueError("The source phrase has unusable acoustic boundaries.")
        cue = sample["subtitle"]
        measured.append(
            {
                "sample": index,
                "cue_index": sample["cue_index"],
                "first_word": first,
                "last_word": last,
                "source_text": " ".join(w["text"] for w in phrase),
                "caption": cue["text"],
                "speech_start": start,
                "speech_end": end,
                "onset_delta": round(cue["start"] - start, 3),
                "end_delta": round(cue["end"] - end, 3),
                "explanation": explanation,
            }
        )
    positions = [s["subtitle"]["start"] for s in samples]
    observed = [samples[i]["subtitle"]["start"] for i in seen]
    span = max(positions) - min(positions)
    if not span or max(observed) - min(observed) < 0.6 * span:
        raise ValueError("Matches must span the sampled timeline, not one short scene.")
    onset = [abs(m["onset_delta"]) for m in measured]
    passed = (
        median(onset) <= 0.75
        and max(onset) <= 1.5
        and all(-1.5 <= m["end_delta"] <= 4 for m in measured)
    )
    return {
        "passed": passed,
        "scope": "sampled_voice_alignment",
        "reviewable": True,
        "reasons": (
            []
            if passed
            else [
                "Matched dialogue is substantially early or late in the sampled track."
            ]
        ),
        "matches": measured,
        "sample_count": len(samples),
        "matched_samples": len(measured),
        "median_timing_error": median(onset),
        "max_timing_error": max(onset),
        "unmatched_samples": sorted(set(range(len(samples))) - seen),
    }

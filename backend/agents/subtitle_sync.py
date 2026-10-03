"""Measure caption timing against audio-timed speech evidence.

Every time comes from tools: caption times from the track, speech onsets from
the speech detector. Offsets are caption start minus speech onset, so a
positive value means captions appear late. One line's measurement is noisy
(32 ms detector frames, deliberate lead-ins, shot-change snapping), so track
and section offsets aggregate many clean onsets. That aggregation is what makes
a ±50 ms correction meaningful. This module never decides that a caption says
the right thing: meaning correspondence is established separately.
"""

from __future__ import annotations

import bisect
import math
from statistics import median

# Detector onset minus true speech onset, measured by the calibration suite
# (tests/subtitle_sync_calibration.py): median 91 ms, 95% CI 77–106 ms over 51
# onsets in clean and noisy English speech. Subtracted before any comparison.
ONSET_BIAS = 0.09
# Corrections place captions where the voice starts. Three professional anime
# tracks measured −32 ms, −189 ms (a deliberate lead-in) and +220 ms (late in
# every section; confirmed against recogniser timing).
TARGET_OFFSET = 0.0
# Late captions feel laggy, so more than 50 ms late is corrected. A lead-in of
# up to 200 ms is a common deliberate convention and is left alone.
LATE_TOLERANCE = 0.05
EARLY_TOLERANCE = 0.2
# Drift is corrected only when it moves captions by more than this.
DRIFT_THRESHOLD = 0.05
# Lines further than this from their speech onset are listed for review.
LINE_TOLERANCE = 0.25


def onsets(evidence, *, clean_only=True):
    """Sorted [(time, utterance_id)] of detector onsets, bias-corrected."""
    return sorted(
        (round(u["onset"] - ONSET_BIAS, 3), u["id"])
        for u in evidence["utterances"]
        if u["onset_source"] == "speech_detector"
        and (u["clean_onset"] or not clean_only)
    )


def _nearest(times, target, limit):
    index = bisect.bisect_left(times, target)
    best = None
    for candidate in (index - 1, index):
        if 0 <= candidate < len(times):
            distance = times[candidate] - target
            if abs(distance) <= limit and (best is None or abs(distance) < abs(best[1])):
                best = (candidate, distance)
    return best


def pair(cues, anchors, expected, window=0.3):
    """Pair each caption start with the nearest onset near its expected time.

    ``expected(start)`` predicts caption start minus speech onset.

    A caption pairs only with an unused onset, nearest first, so two captions
    cannot both claim one utterance.
    """
    times = [t for t, _ in anchors]
    candidates = []
    for index, cue in enumerate(cues):
        found = _nearest(times, cue["start"] - expected(cue["start"]), window)
        if found:
            candidates.append((abs(found[1]), index, found[0]))
    used, pairs = set(), []
    for _, cue_index, onset_index in sorted(candidates):
        if onset_index in used or any(p["cue"] == cue_index for p in pairs):
            continue
        used.add(onset_index)
        time, identity = anchors[onset_index]
        pairs.append(
            {
                "cue": cue_index,
                "utterance": identity,
                "caption_start": cues[cue_index]["start"],
                "speech_onset": time,
                "delta": round(cues[cue_index]["start"] - time, 3),
            }
        )
    return sorted(pairs, key=lambda p: p["caption_start"])


def _spread(values):
    centre = median(values)
    return 1.4826 * median(abs(v - centre) for v in values)


def theil_sen(points):
    """Robust slope/intercept of delta against time; tolerant of outlier lines."""
    if len(points) < 2:
        return 0.0, median(y for _, y in points) if points else 0.0
    step = max(1, len(points) // 400)
    sample = points[::step]
    slopes = [
        (b[1] - a[1]) / (b[0] - a[0])
        for i, a in enumerate(sample)
        for b in sample[i + 1 :]
        if b[0] - a[0] > 1
    ]
    slope = median(slopes) if slopes else 0.0
    return slope, median(y - slope * x for x, y in points)


# Frame-rate conversions behind most real drift: NTSC film, PAL speed-up and
# 24↔25 fps. The coarse search tries each before refining a free slope.
FILM = 24000 / 1001
RATIOS = (1.0, 1001 / 1000, 1000 / 1001, 25 / FILM, FILM / 25, 25 / 24, 24 / 25)


def _coarse(starts, times, search, step, tolerance):
    """Best (count, ratio, offset) where predicted onset = start × ratio − offset."""
    import numpy as np

    starts, times = np.asarray(starts), np.asarray(times)
    offsets = np.arange(-round(search / step), round(search / step) + 1) * step
    results = []
    for ratio in RATIOS:
        targets = starts[None, :] * ratio - offsets[:, None]
        index = np.clip(np.searchsorted(times, targets), 1, len(times) - 1)
        distance = np.minimum(
            np.abs(times[index] - targets), np.abs(times[index - 1] - targets)
        )
        counts = (distance <= tolerance).sum(axis=1)
        results.append((ratio, offsets, counts))
    best = max(
        (int(counts[i]), -abs(ratio - 1), -abs(offsets[i]), ratio, float(offsets[i]))
        for ratio, offsets, counts in results
        for i in range(len(offsets))
    )
    count, _, _, ratio, offset = best
    same = next(c for r, _, c in results if r == ratio)
    rival = max(
        (int(same[i]) for i in range(len(offsets)) if abs(offsets[i] - offset) > 1.0),
        default=0,
    )
    return count, ratio, offset, rival


def estimate(cues, evidence, *, search=10.0, step=0.02, tolerance=0.2, window=180.0):
    """Offset, drift and section profile of a track against speech onsets.

    A coarse search over shifts and standard frame-rate ratios finds where
    most caption starts coincide with clean onsets, then repeated pairing and
    a robust line fit refine it. ``prominence`` compares that peak with the
    best clearly different shift; an unrelated track has no distinct peak, so
    ``consistent`` is false and no correction is proposed.
    """
    anchors = onsets(evidence)
    times = [t for t, _ in anchors]
    starts = [c["start"] for c in cues]
    if len(anchors) < 8 or not starts:
        return {
            "measurable": False,
            "reason": "Too few sharp speech onsets to measure timing.",
            "anchor_count": len(anchors),
        }
    count, ratio, coarse, rival = _coarse(starts, times, search, step, tolerance)
    intercept, slope = coarse, 1 - ratio
    pairs = []
    for width in (0.3, 0.3, 0.25, 0.25):
        found = pair(cues, anchors, lambda t: intercept + slope * t, width)
        if len(found) < 5:
            break
        pairs = found
        slope, intercept = theil_sen([(p["caption_start"], p["delta"]) for p in pairs])
    if len(pairs) < 5:
        return {
            "measurable": False,
            "reason": "Captions rarely coincide with speech onsets at any shift.",
            "anchor_count": len(anchors),
            "coincidences": count,
        }
    points = [(p["caption_start"], p["delta"]) for p in pairs]
    residuals = [y - intercept - slope * x for x, y in points]
    deltas = [p["delta"] for p in pairs]
    spread = _spread(residuals)
    mean_time = sum(x for x, _ in points) / len(points)
    time_spread = math.sqrt(sum((x - mean_time) ** 2 for x, _ in points) / len(points))
    slope_error = spread / (math.sqrt(len(points)) * max(time_spread, 1.0))
    duration = max(c["end"] for c in cues)
    sections = []
    for first in range(math.ceil(duration / window)):
        inside = [
            (p["delta"], r)
            for p, r in zip(pairs, residuals)
            if first * window <= p["caption_start"] < (first + 1) * window
        ]
        if inside:
            sections.append(
                {
                    "start": first * window,
                    "end": round(min(duration, (first + 1) * window), 3),
                    "pairs": len(inside),
                    "offset": round(median(d for d, _ in inside), 3),
                    "departure": round(median(r for _, r in inside), 3),
                }
            )
    match = len(pairs) / max(1, min(len(anchors), len(cues)))
    prominence = count / max(1, rival)
    return {
        "measurable": True,
        "consistent": prominence >= 1.5 and match >= 0.35,
        "offset": round(median(deltas), 3),
        "offset_ci95": round(1.96 * 1.2533 * _spread(deltas) / math.sqrt(len(deltas)), 3),
        "line_spread": round(spread, 3),
        "intercept": round(intercept, 3),
        "slope": slope,
        "frame_rate_ratio": ratio,
        "drift_ms_per_minute": round(slope * 60000, 1),
        "drift_over_track": round(slope * duration, 3),
        "drift_over_track_ci95": round(1.96 * slope_error * duration, 3),
        "anchor_count": len(anchors),
        "paired_captions": len(pairs),
        "caption_count": len(cues),
        "anchor_match": round(match, 3),
        "coincidences": count,
        "prominence": round(prominence, 2),
        "sections": sections,
        "outliers": [
            {**p, "residual": round(r, 3)}
            for p, r in zip(pairs, residuals)
            if abs(r) > LINE_TOLERANCE
        ],
        "pairs": pairs,
    }


def correction(measured):
    """The least invasive timing correction the measurement justifies.

    Returns None when the track already sits within tolerance: at most 50 ms
    late or 200 ms early. Drift is only corrected when it moves captions by
    more than the threshold across the track; otherwise a constant shift is
    enough.
    """
    if not measured.get("measurable") or not measured["consistent"]:
        return None
    error = measured["offset"] - TARGET_OFFSET
    drift = measured["drift_over_track"]
    confident = measured["offset_ci95"] < max(LATE_TOLERANCE / 2, abs(error) / 2)
    if (
        abs(drift) > DRIFT_THRESHOLD
        and abs(drift) > measured["drift_over_track_ci95"]
        and measured["paired_captions"] >= 20
    ):
        return {
            "kind": "drift",
            "intercept": round(measured["intercept"] - TARGET_OFFSET, 3),
            "slope": measured["slope"],
        }
    if (error > LATE_TOLERANCE or error < -EARLY_TOLERANCE) and confident:
        return {"kind": "shift", "seconds": round(-error, 3)}
    return None


def apply(cues, fix):
    """Return corrected copies of the cues; the originals are never modified."""
    if not fix:
        return [dict(c) for c in cues]
    out = []
    for cue in cues:
        if fix["kind"] == "shift":
            delta = fix["seconds"]
        else:
            delta = -(fix["intercept"] + fix["slope"] * cue["start"])
        out.append(
            {
                **cue,
                "start": round(max(0.0, cue["start"] + delta), 3),
                "end": round(max(0.001, cue["end"] + delta), 3),
            }
        )
    return out

"""Isolated built-in subtitle alignment and independent sampled audio inspection.

The coordinator never treats an aligner's exit status as proof of subtitle quality.
FFsubsync supplies a timing candidate. A separately transcribed set of real audio
samples supplies content and timing evidence for validation and agent review.
"""

from __future__ import annotations
import hashlib
import html
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from statistics import median


def words(text):
    return re.findall(r"[^\W_]+", text.casefold(), re.UNICODE)


def cues_from_text(text, format_name):
    import pysubs2

    if format_name not in ("srt", "vtt", "ass", "ssa"):
        raise ValueError("Use an SRT, VTT or ASS text subtitle.")
    if len(text.encode("utf8")) > 2 * 1024 * 1024:
        raise ValueError("Subtitle files must be smaller than 2 MB.")
    parsed = pysubs2.SSAFile.from_string(text, format_=format_name)
    cues = []
    for entry in parsed:
        clean = entry.plaintext.strip()
        if format_name == "vtt":
            clean = html.unescape(clean)
        if entry.is_comment or not clean:
            continue
        if entry.start < 0 or entry.end <= entry.start:
            raise ValueError("The subtitle contains invalid cue timings.")
        if len(clean) > 2000:
            raise ValueError("A subtitle cue is too large to display safely.")
        cues.append(
            {"start": entry.start / 1000, "end": entry.end / 1000, "text": clean}
        )
    if not cues or len(cues) > 20000:
        raise ValueError("Choose a subtitle with 1–20,000 readable cues.")
    if any(a["start"] > b["start"] for a, b in zip(cues, cues[1:])):
        raise ValueError("Subtitle cues are out of order.")
    return cues


def stamp(seconds, separator="."):
    milliseconds = round(max(0, seconds) * 1000)
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def render(cues, vtt=False):
    lines = ["WEBVTT", ""] if vtt else []
    for index, cue in enumerate(cues, 1):
        lines.extend(
            [
                str(index),
                f'{stamp(cue["start"],"." if vtt else ",")} --> {stamp(cue["end"],"." if vtt else ",")}',
                html.escape(cue["text"], quote=False) if vtt else cue["text"],
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def sample_cues(cues):
    substantial = [(i, c) for i, c in enumerate(cues) if len(words(c["text"])) >= 3]
    if not substantial:
        return []
    count = min(5, len(substantial))
    indices = sorted(
        set(round((len(substantial) - 1) * (i + 0.5) / count) for i in range(count))
    )
    return [substantial[i] for i in indices]


def transcribe_samples(
    video, cues, audio_index, folder, model_path, ffmpeg, audio_language=None
):
    from faster_whisper import WhisperModel

    if not model_path or not (Path(model_path) / "model.bin").is_file():
        raise ValueError(
            "The packaged speech model is unavailable. Repair will resume after the node’s media components are installed."
        )
    model = WhisperModel(
        str(model_path),
        device="cpu",
        compute_type="int8",
        cpu_threads=2,
        num_workers=1,
        local_files_only=True,
    )
    samples = []
    for number, (cue_index, cue) in enumerate(sample_cues(cues)):
        start = max(0, (cue["start"] + cue["end"]) / 2 - 15)
        audio = folder / f"sample-{number}.wav"
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-ss",
                str(start),
                "-i",
                str(video),
                "-t",
                "30",
                "-map",
                f"0:{audio_index}",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-y",
                str(audio),
            ],
            check=True,
            timeout=60,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        segments, info = model.transcribe(
            str(audio),
            beam_size=3,
            word_timestamps=True,
            condition_on_previous_text=False,
            language=(
                audio_language
                if audio_language and re.fullmatch("[a-z]{2}", audio_language)
                else None
            ),
            vad_filter=True,
        )
        transcript = []
        spoken = []
        for segment in segments:
            transcript.append(segment.text.strip())
            for word in segment.words or []:
                if not math.isfinite(word.start) or not math.isfinite(word.end):
                    continue
                spoken.append(
                    {
                        "text": word.word.strip(),
                        "start": round(start + word.start, 3),
                        "end": round(start + word.end, 3),
                        "probability": round(word.probability, 3),
                    }
                )
        samples.append(
            {
                "cue_index": cue_index,
                "start": start,
                "end": start + 30,
                "subtitle": cue,
                "language": info.language,
                "language_probability": round(info.language_probability, 3),
                "transcript": " ".join(transcript),
                "words": spoken,
            }
        )
        audio.unlink(missing_ok=True)
    return samples


def inspect_evidence(cues, samples, duration, language):
    """Measure text agreement and residual timing independently of the aligner.

    This conservative gate handles same-language dialogue. Translated tracks
    retain their evidence for semantic review; they are never declared correct
    merely because speech activity happened to correlate.
    """
    from difflib import SequenceMatcher
    from .media_state import language_code

    reasons = []
    matches = []
    if cues[-1]["end"] > duration + 0.75:
        reasons.append("Some captions extend beyond this media copy.")
    if any(c["end"] - c["start"] > 45 for c in cues):
        reasons.append("Some captions remain on screen unusually long.")
    for sample in samples:
        cue = sample["subtitle"]
        target = words(cue["text"])
        spoken = []
        for word in sample["words"]:
            if word.get("probability", 1) < 0.15:
                continue
            for token in words(word["text"]):
                spoken.append({**word, "token": token})
        same_language = language_code(sample["language"]) == language_code(language)
        best = {
            "score": 0.0,
            "start_error": None,
            "end_error": None,
            "same_language": same_language,
            "cue_index": sample["cue_index"],
        }
        if same_language:
            for first in range(len(spoken)):
                for length in range(
                    max(2, len(target) - 3),
                    min(len(spoken) - first, len(target) + 4) + 1,
                ):
                    part = spoken[first : first + length]
                    score = SequenceMatcher(
                        None, target, [w["token"] for w in part], autojunk=False
                    ).ratio()
                    credible = [w for w in part if w.get("probability", 1) >= 0.5]
                    if len(credible) < max(2, len(part) // 2):
                        continue
                    if score > best["score"]:
                        best.update(
                            {
                                "score": round(score, 3),
                                "start_error": round(
                                    cue["start"] - credible[0]["start"], 3
                                ),
                                "end_error": round(cue["end"] - credible[-1]["end"], 3),
                                "heard": " ".join(w["text"] for w in part),
                            }
                        )
        matches.append(best)
    strong = [m for m in matches if m["score"] >= 0.65]
    errors = [max(abs(m["start_error"]), abs(m["end_error"])) for m in strong]
    minimum = min(3, len(sample_cues(cues)))
    content_fraction = len(strong) / max(1, len(matches))
    if not matches:
        reasons.append("There is not enough transcribed dialogue to verify this track.")
    if matches and any(not m["same_language"] for m in matches):
        reasons.append(
            "This translated track needs semantic review against the sampled dialogue."
        )
    if len(strong) < minimum or content_fraction < 0.8:
        reasons.append("The subtitle text does not reliably match the sampled audio.")
    if errors and (median(errors) > 0.65 or max(errors) > 1.25):
        reasons.append(
            "Timing still differs from the spoken words in one or more samples."
        )
    return {
        "passed": not reasons,
        "reasons": reasons,
        "sample_count": len(matches),
        "strong_matches": len(strong),
        "content_fraction": round(content_fraction, 3),
        "median_timing_error": round(median(errors), 3) if errors else None,
        "max_timing_error": round(max(errors), 3) if errors else None,
        "matches": matches,
        "thresholds": {
            "text_score": 0.65,
            "content_fraction": 0.8,
            "median_error_seconds": 0.65,
            "max_error_seconds": 1.25,
        },
    }


def process(packet):
    from ffsubsync.ffsubsync import make_parser, run
    from .media_state import file_version

    folder = Path(packet["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    video = Path(packet["video"])
    if file_version(video) != packet["version"]:
        raise ValueError("The video changed before subtitle preparation.")
    original = packet["text"]
    cues = cues_from_text(original, packet["format"])
    if cues[-1]["end"] > packet["duration"] + 120:
        raise ValueError(
            "These subtitles appear to belong to a different-length video."
        )
    (folder / "original.txt").write_text(original, encoding="utf8")
    source = folder / "input.srt"
    source.write_text(render(cues), encoding="utf8")
    output = folder / "aligned.srt"
    args = make_parser().parse_args(
        [
            str(video),
            "-i",
            str(source),
            "-o",
            str(output),
            "--reference-stream",
            f'0:{packet["audio_index"]}',
            "--vad",
            "webrtc",
            "--max-offset-seconds",
            "60",
            "--skip-infer-framerate-ratio",
            "--gss",
            "--ffmpeg-path",
            str(Path(packet["ffmpeg"]).parent),
        ]
    )
    result = run(args)
    if not result.get("sync_was_successful") or not output.is_file():
        raise ValueError(
            "Alignment did not find a reliable timing candidate for this video."
        )
    repaired = cues_from_text(output.read_text(encoding="utf-8-sig"), "srt")
    samples = transcribe_samples(
        video,
        repaired,
        packet["audio_index"],
        folder,
        packet["model_path"],
        packet["ffmpeg"],
        packet.get("audio_language"),
    )
    quality = inspect_evidence(
        repaired, samples, packet["duration"], packet["language"]
    )
    original_samples = [
        {**sample, "subtitle": cues[sample["cue_index"]]}
        for sample in samples
        if sample["cue_index"] < len(cues)
    ]
    original_quality = inspect_evidence(
        cues, original_samples, packet["duration"], packet["language"]
    )
    unchanged = original_quality["passed"]
    if unchanged:
        repaired, samples, quality = cues, original_samples, original_quality
    if file_version(video) != packet["version"]:
        raise ValueError("The video changed while its subtitles were being prepared.")
    (folder / "prepared.vtt").write_text(render(repaired, vtt=True), encoding="utf8")
    (folder / "original.vtt").write_text(render(cues, vtt=True), encoding="utf8")
    return {
        "alignment": {
            "offset_seconds": 0 if unchanged else result.get("offset_seconds"),
            "scale": 1 if unchanged else result.get("framerate_scale_factor"),
        },
        "unchanged": unchanged,
        "original_quality": original_quality,
        "quality": quality,
        "samples": samples,
        "cue_count": len(repaired),
        "original_sha256": hashlib.sha256(original.encode("utf8")).hexdigest(),
        "tools": {
            "alignment": "ffsubsync 0.5.1",
            "speech": "faster-whisper 1.2.1 / base",
            "speech_mode": "local CPU int8",
        },
        "prepared_path": "prepared.vtt",
        "original_path": "original.vtt",
    }


def main(path):
    request = Path(path)
    try:
        packet = json.loads(request.read_text(encoding="utf8"))
        result = {"ok": True, "value": process(packet)}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)[:2000]}
    request.with_name("result.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf8"
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))

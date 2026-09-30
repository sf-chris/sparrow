"""Isolated audio-only observations for an opt-in subtitle investigation.

Run this file with the interpreter containing the requested recogniser. No
caption text, previous transcript or prompt is accepted as recognition input.
Whisper translations are content evidence, not original-language word timings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import time
import wave
from pathlib import Path


def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(".pending")
    with open(temporary, "x", encoding="utf8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    os.replace(temporary, path)


def run(request, output):
    import numpy as np

    started = time.monotonic()
    config = json.loads(Path(request).read_text())
    output = Path(output)
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    write(output / "request.json", config)
    if set(config) - {
        "audio_path",
        "audio_sha256",
        "model_path",
        "backend",
        "threads",
        "ranges",
        "model_language",
        "qwen_dtype",
    }:
        raise ValueError(
            "Unknown recognition option; failed experimental profiles cannot be silently reused."
        )
    audio_path = Path(config["audio_path"]).resolve(strict=True)
    if digest(audio_path) != config["audio_sha256"]:
        raise ValueError("Audio source changed.")
    with wave.open(str(audio_path)) as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (
            16000,
            1,
            2,
        ):
            raise ValueError("Use playback-clock mono 16 kHz PCM16 audio.")
        duration = audio.getnframes() / 16000
    ranges = config["ranges"]
    if not 1 <= len(ranges) <= 240:
        raise ValueError("Use a bounded batch of 1–240 observations.")
    for row in ranges:
        if set(row) - {"id", "start", "end", "language", "task"}:
            raise ValueError(
                "Recognition takes only a range and language/task hypothesis."
            )
        start, end = row["start"], row["end"]
        if not (
            all(math.isfinite(t) for t in (start, end))
            and 0 <= start < end <= duration
            and end - start <= 60
        ):
            raise ValueError(
                "Each range must be within the audio and at most 60 seconds."
            )
        if row.get("task", "transcribe") not in {"transcribe", "translate"}:
            raise ValueError("Unknown recognition task.")
    threads = config.get("threads", 2)
    if not 1 <= threads <= 8:
        raise ValueError("Use 1–8 inference threads.")
    model_path = Path(config["model_path"]).resolve(strict=True)
    backend = config.get("backend", "whisper")
    qwen_dtype = config.get("qwen_dtype", "float32")
    if qwen_dtype not in {"float32", "bfloat16"} or (
        "qwen_dtype" in config and backend != "qwen"
    ):
        raise ValueError("qwen_dtype applies only to Qwen: float32 or bfloat16.")
    if backend == "qwen" and Path("/proc/meminfo").exists():
        info = dict(
            line.split(":", 1)
            for line in Path("/proc/meminfo").read_text().splitlines()
        )
        available = int(info["MemAvailable"].split()[0]) * 1024
        weights = sum(p.stat().st_size for p in model_path.glob("*.safetensors"))
        required = weights * (3 if qwen_dtype == "float32" else 2) + 1024**3
        if available < required:
            write(
                output / "resource-refusal.json",
                {
                    "state": "resource_limited",
                    "available_bytes": available,
                    "required_bytes": required,
                    "reason": "Reserve CPU loading/inference headroom before starting this Qwen profile.",
                    "qwen_dtype": qwen_dtype,
                    "verified": False,
                },
            )
            raise ValueError(
                "Not enough currently available RAM for this Qwen CPU profile; use a smaller model or retry when other processing ends."
            )
    if backend == "whisper":
        from faster_whisper import WhisperModel

        model = WhisperModel(
            str(model_path),
            device="cpu",
            compute_type="int8",
            cpu_threads=threads,
            local_files_only=True,
        )
        model_hash = digest(model_path / "model.bin")
    elif backend == "qwen":
        import torch
        from qwen_asr import Qwen3ASRModel

        torch.set_num_threads(threads)
        model = Qwen3ASRModel.from_pretrained(
            str(model_path),
            dtype=getattr(torch, qwen_dtype),
            device_map="cpu",
            max_inference_batch_size=1,
            max_new_tokens=256,
        )
        model_hash = {
            p.name: digest(p) for p in sorted(model_path.glob("*.safetensors"))
        }
    elif backend == "ctc":
        import torch
        from transformers import AutoProcessor, AutoModelForCTC

        model_language = config.get("model_language", "en")
        if model_language not in {"en", "pa"} or any(
            r.get("task", "transcribe") != "transcribe"
            or r.get("language") not in (None, model_language)
            for r in ranges
        ):
            raise ValueError(
                "Use the CTC model's configured source language and transcribe task."
            )
        torch.set_num_threads(threads)
        processor = AutoProcessor.from_pretrained(
            str(model_path), local_files_only=True
        )
        model = AutoModelForCTC.from_pretrained(
            str(model_path), local_files_only=True, weights_only=True
        ).eval()
        model_hash = {
            p.name: digest(p)
            for p in sorted(model_path.iterdir())
            if p.suffix in {".safetensors", ".bin", ".json"}
        }
    else:
        raise ValueError("Choose a configured recogniser.")
    write(
        output / "model.json",
        {
            "backend": backend,
            "path": str(model_path),
            "sha256": model_hash,
            "threads": threads,
            "device": "cpu",
        },
    )
    with wave.open(str(audio_path)) as audio:
        for index, spec in enumerate(ranges):
            tick = time.monotonic()
            start, end = spec["start"], spec["end"]
            audio.setpos(round(start * 16000))
            pcm = audio.readframes(round((end - start) * 16000))
            samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
            row = {
                **spec,
                "language_hypothesis": spec.get("language"),
                "index": index,
                "audio_sha256": config["audio_sha256"],
                "clip_pcm_sha256": hashlib.sha256(pcm).hexdigest(),
                "backend": backend,
                "words": [],
                "segments": [],
                "caption_conditioned": False,
            }
            if backend == "whisper":
                options = dict(
                    language=spec.get("language"),
                    task=spec.get("task", "transcribe"),
                    word_timestamps=True,
                    beam_size=5,
                    condition_on_previous_text=False,
                    vad_filter=False,
                    temperature=(0, 0.2, 0.4),
                    max_new_tokens=160,
                )
                row["options"] = options
                segments, info = model.transcribe(samples, **options)
                row.update(
                    language=info.language,
                    language_probability=float(info.language_probability),
                )
                for segment in segments:
                    row["segments"].append(
                        {
                            "text": segment.text,
                            "start": round(start + float(segment.start), 3),
                            "end": round(start + float(segment.end), 3),
                            "avg_logprob": float(segment.avg_logprob),
                            "no_speech_probability": float(segment.no_speech_prob),
                            "compression_ratio": float(segment.compression_ratio),
                            "temperature": float(segment.temperature),
                        }
                    )
                    for word in segment.words or []:
                        valid = (
                            all(
                                math.isfinite(v)
                                for v in (word.start, word.end, word.probability)
                            )
                            and 0 <= word.start < word.end <= end - start + 0.05
                        )
                        row["words"].append(
                            {
                                "id": f"w{index:05d}-{len(row['words']):04d}",
                                "text": word.word.strip(),
                                "start": (
                                    round(start + float(word.start), 3)
                                    if math.isfinite(word.start)
                                    else None
                                ),
                                "end": (
                                    round(start + float(word.end), 3)
                                    if math.isfinite(word.end)
                                    else None
                                ),
                                "probability": (
                                    float(word.probability)
                                    if math.isfinite(word.probability)
                                    else None
                                ),
                                "usable_timing": bool(
                                    valid
                                    and spec.get("task", "transcribe") == "transcribe"
                                ),
                            }
                        )
                row["text"] = " ".join(s["text"].strip() for s in row["segments"])
            elif backend == "ctc":
                inputs = processor(samples, sampling_rate=16000, return_tensors="pt")
                with torch.inference_mode():
                    probabilities = model(**inputs).logits[0].softmax(-1)
                ids = probabilities.argmax(-1)
                decoded = processor.tokenizer.decode(ids, output_word_offsets=True)
                unexpected_tokens = [
                    token
                    for token in processor.tokenizer.all_special_tokens
                    if token in decoded.text
                ]
                ratio = model.config.inputs_to_logits_ratio / 16000
                row.update(
                    language=model_language,
                    task="transcribe",
                    text=decoded.text,
                    timing_method="greedy CTC character offsets; independent of candidate captions",
                    quality_flags=(
                        ["unexpected_special_tokens_in_decoded_speech"]
                        if unexpected_tokens
                        else []
                    ),
                )
                for word in decoded.word_offsets:
                    a, b = word["start_offset"], word["end_offset"]
                    row["words"].append(
                        {
                            "id": f"w{index:05d}-{len(row['words']):04d}",
                            "text": word["word"],
                            "start": round(start + a * ratio, 3),
                            "end": round(start + b * ratio, 3),
                            "probability": float(
                                probabilities[a:b].max(-1).values.mean()
                            ),
                            "usable_timing": bool(
                                not unexpected_tokens
                                and 0 <= a < b
                                and b * ratio <= end - start + 0.05
                            ),
                        }
                    )
            else:
                if spec.get("task", "transcribe") != "transcribe":
                    raise ValueError("Qwen ASR does not supply a translation task.")
                language = {"ar": "Arabic", "en": "English", "hi": "Hindi"}.get(
                    spec.get("language"), spec.get("language")
                )
                results = model.transcribe(audio=(samples, 16000), language=language)
                row.update(
                    text=results[0].text,
                    language=results[0].language,
                    timing_scope="Audio clip only; no Arabic word alignment supplied by this recogniser.",
                )
            row["processing_seconds"] = time.monotonic() - tick
            write(output / f"observation-{index:04d}.json", row)
            print(
                json.dumps(
                    {
                        "index": index,
                        "id": spec.get("id"),
                        "language": row.get("language"),
                        "task": spec.get("task", "transcribe"),
                        "text": row["text"],
                        "seconds": round(row["processing_seconds"], 2),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    write(
        output / "complete.json",
        {
            "observations": len(ranges),
            "seconds": time.monotonic() - started,
            "peak_process_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            / 1024,
            "verified": False,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.request, args.output)

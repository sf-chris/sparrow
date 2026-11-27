"""Where speech recognition runs: the graphics card when it works, else the processor.

Runs inside the speech workers, so it imports only the standard library until a
model is loaded. A graphics card that fails (missing CUDA libraries, an
unsupported card, out of memory) is remembered in the computer's speech folder,
the work carries on with the processor, and the computer's Subtitles row says
why. SPARROW_SPEECH_DEVICE=cpu|cuda|auto overrides the choice.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

FAILURE = "cuda-failure.json"
# How long a failed graphics card is left alone before it is tried again.
RETRY_SECONDS = 7 * 86400
CUDA_WORDS = ("cuda", "cudnn", "cublas", "libcu", "nvrtc", "gpu", "out of memory")


def state_dir():
    folder = os.getenv("SPARROW_SPEECH_STATE")
    return Path(folder) if folder else None


def failure(folder=None):
    """Why the graphics card was last set aside, if it still is."""
    folder = folder or state_dir()
    if not folder:
        return None
    try:
        value = json.loads((Path(folder) / FAILURE).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    if time.time() - float(value.get("at", 0)) > RETRY_SECONDS:
        return None
    return value


def remember_failure(exc, folder=None):
    folder = folder or state_dir()
    if not folder:
        return
    Path(folder).mkdir(parents=True, exist_ok=True)
    pending = Path(folder) / (FAILURE + ".pending")
    pending.write_text(
        json.dumps({"at": time.time(), "reason": (str(exc) or type(exc).__name__)[:300]}),
        encoding="utf8",
    )
    os.replace(pending, Path(folder) / FAILURE)


def forget_failure(folder=None):
    folder = folder or state_dir()
    if folder:
        (Path(folder) / FAILURE).unlink(missing_ok=True)


def is_cuda_failure(exc) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(word in text for word in CUDA_WORDS)


def cuda_compute(ctranslate2) -> str:
    """The fastest accurate number format this card supports."""
    try:
        supported = set(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:
        supported = set()
    for choice in ("float16", "int8_float16", "int8", "float32"):
        if choice in supported:
            return choice
    return "float16"


def wants_cuda(ctranslate2, folder=None) -> bool:
    choice = os.getenv("SPARROW_SPEECH_DEVICE", "auto").lower()
    if choice == "cpu":
        return False
    if choice != "cuda" and failure(folder):
        return False
    try:
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def load(model_path, threads, *, folder=None, factory=None):
    """(model, "cuda float16" or "cpu int8") for a faster-whisper model folder."""
    import ctranslate2

    if factory is None:
        from faster_whisper import WhisperModel as factory
    if wants_cuda(ctranslate2, folder):
        compute = cuda_compute(ctranslate2)
        try:
            model = factory(
                str(model_path),
                device="cuda",
                compute_type=compute,
                num_workers=1,
                local_files_only=True,
            )
            return model, f"cuda {compute}"
        except Exception as exc:
            if os.getenv("SPARROW_SPEECH_DEVICE", "auto").lower() == "cuda":
                raise
            remember_failure(exc, folder)
    return (
        factory(
            str(model_path),
            device="cpu",
            compute_type="int8",
            cpu_threads=threads,
            num_workers=1,
            local_files_only=True,
        ),
        "cpu int8",
    )


class Recogniser:
    """A loaded model that moves itself to the processor if the card fails mid-run."""

    def __init__(self, model_path, threads, *, folder=None, factory=None):
        self.model_path, self.threads = model_path, threads
        self.folder, self.factory = folder, factory
        self.model, self.compute = load(model_path, threads, folder=folder, factory=factory)
        self.fallback = None

    def run(self, work):
        """Call work(model); on a graphics-card failure, retry once on the processor."""
        try:
            return work(self.model)
        except Exception as exc:
            if not self.compute.startswith("cuda") or not is_cuda_failure(exc):
                raise
            remember_failure(exc, self.folder)
            self.fallback = str(exc)[:300]
            self.model, self.compute = load(
                self.model_path, self.threads, folder=self.folder, factory=self.factory
            )
            if self.compute.startswith("cuda"):  # Override forced CUDA: nothing else to try.
                raise
            return work(self.model)

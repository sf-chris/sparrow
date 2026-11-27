"""The speech engine behind a computer's Subtitles job.

This server's image carries faster-whisper and its models. A paired computer
sets them up by itself when Subtitles is turned on, without administrator
rights: uv makes a private Python environment in the node's folder, installs
the engine pinned with hashes (plus NVIDIA's CUDA libraries when there is a
graphics card), and the speech model comes from this Sparrow server, not the
internet. Every step is recorded, so running setup again only does what is
left. Turning Subtitles off stops setup and keeps what was installed.

What the computer reports as capabilities["speech"]:
  ready, device ("cuda" or "cpu"), gpu (the card's name, when there is one),
  installing, progress (0-100), error (plain words), engine and model.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .speech_device import failure as cuda_failure

SETUP_VERSION = 1
REQUIREMENTS = Path(__file__).with_name("speech_requirements.txt")
# NVIDIA's CUDA 12 libraries that CTranslate2 4.x loads for the graphics card.
GPU_PACKAGES = ("nvidia-cublas-cu12==12.*", "nvidia-cudnn-cu12==9.*")
# The model a computer fetches, best first, from whatever this server has.
MODEL_ORDER = ("whisper-large-v3-turbo", "whisper-medium", "whisper-base")
MODEL_NAME = re.compile(r"^whisper-[a-z0-9.-]{1,40}$")
MODEL_FILE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
APP_ROOT = Path(__file__).resolve().parents[2]
STEPS = {"uv": 5, "environment": 10, "engine": 45, "gpu": 65, "model": 95, "check": 100}


def folder(data_dir) -> Path:
    return Path(data_dir) / "speech"


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(json.dumps(value, sort_keys=True), encoding="utf8")
    os.replace(pending, path)


# ─── The graphics card ───────────────────────────────────────────────


@functools.lru_cache(maxsize=1)
def _gpu_cached(_bucket):
    tool = shutil.which("nvidia-smi")
    if not tool and os.name == "nt":
        candidate = Path(os.getenv("ProgramFiles", "C:/Program Files")) / "NVIDIA Corporation/NVSMI/nvidia-smi.exe"
        tool = str(candidate) if candidate.is_file() else None
    if tool:
        try:
            names = subprocess.run(
                [tool, "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
            )
            first = names.stdout.strip().splitlines()[0].strip() if names.returncode == 0 and names.stdout.strip() else ""
            if first:
                return first
        except (OSError, subprocess.SubprocessError, IndexError):
            pass
    for information in sorted(Path("/proc/driver/nvidia/gpus").glob("*/information")):
        try:
            for line in information.read_text().splitlines():
                key, _, value = line.partition(":")
                if key.strip() == "Model" and value.strip():
                    return value.strip()
        except OSError:
            continue
    return None


def gpu_name():
    """The NVIDIA card's name when its driver answers; checked every ten minutes."""
    if os.getenv("SPARROW_SPEECH_DEVICE", "").lower() == "cpu":
        return None
    return _gpu_cached(int(time.time() // 600))


# ─── This computer's engine ─────────────────────────────────────────


def builtin() -> bool:
    """Whether the Python running Sparrow already has the speech engine."""
    return bool(importlib.util.find_spec("faster_whisper") and importlib.util.find_spec("ctranslate2"))


@functools.lru_cache(maxsize=1)
def _builtin_cuda():
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def engine(data_dir) -> dict:
    """The installed private engine, or {} when there is none yet."""
    value = _load(folder(data_dir) / "engine.json")
    if value.get("setup_version") != SETUP_VERSION or not Path(value.get("python", "")).is_file():
        return {}
    return value


def model_ready(data_dir) -> bool:
    from .subtitle_node import evidence_model

    return evidence_model(data_dir) is not None


def python(data_dir) -> str:
    """The Python that runs speech work on this computer."""
    if builtin() or getattr(sys, "frozen", False):
        return sys.executable
    return engine(data_dir).get("python") or sys.executable


def environment(data_dir) -> dict:
    """Environment for a speech worker: Sparrow's code, CUDA libraries, state folder."""
    env = dict(os.environ)
    env["SPARROW_SPEECH_STATE"] = str(folder(data_dir))
    if not getattr(sys, "frozen", False):
        env["PYTHONPATH"] = os.pathsep.join(
            [str(APP_ROOT), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
        )
    libraries = engine(data_dir).get("library_paths") or []
    if libraries:
        name = "PATH" if os.name == "nt" else "LD_LIBRARY_PATH"
        env[name] = os.pathsep.join([*libraries, *([env[name]] if env.get(name) else [])])
    return env


_status_cache = {}


def status(data_dir) -> dict:
    """capabilities["speech"]: whether this computer can listen, and on what.

    Heartbeats ask often, so the answer is kept for a few seconds.
    """
    key = str(data_dir)
    cached = _status_cache.get(key)
    if cached and time.monotonic() - cached[0] < 5:
        return dict(cached[1])
    value = _status(data_dir)
    _status_cache[key] = (time.monotonic(), value)
    return dict(value)


def _status(data_dir) -> dict:
    gpu = gpu_name()
    problem = cuda_failure(folder(data_dir))
    state = _load(folder(data_dir) / "state.json")
    if getattr(sys, "frozen", False):
        # The Windows installer bundles the processor engine; no graphics card yet.
        return {
            "ready": model_ready(data_dir),
            "device": "cpu",
            "gpu": None,
            "engine": "bundled",
        }
    if builtin():
        cuda = bool(gpu) and _builtin_cuda() and not problem
        report = {"ready": model_ready(data_dir), "device": "cuda" if cuda else "cpu", "gpu": gpu, "engine": "builtin"}
    else:
        installed = engine(data_dir)
        ready = bool(installed) and model_ready(data_dir)
        cuda = bool(gpu) and installed.get("cuda") and not problem
        report = {
            "ready": ready,
            "device": "cuda" if cuda else "cpu",
            "gpu": gpu,
            "engine": "private" if installed else None,
        }
        if state.get("state") == "installing" and not ready:
            report.update(installing=True, progress=state.get("progress", 0))
        elif state.get("state") == "failed" and not ready:
            report["error"] = state.get("error") or "Subtitle setup stopped."
    if gpu and problem:
        report["gpu_problem"] = problem.get("reason", "")
    return report


# ─── Setting it up on a paired computer ─────────────────────────────


class SetupError(Exception):
    """A setup step that stopped, in words for the computer's Subtitles row."""


def _run(command, *, env=None, timeout=1800):
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=timeout)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
    return result.stdout


class NodeSpeech:
    """Sets up and keeps the speech engine on a paired computer.

    fetch_models() returns the server's model listing; fetch_file(name, file,
    offset) yields that file's bytes from offset. Both use the node's own
    credential. Steps already done are skipped, so a repair re-run resumes.
    """

    def __init__(self, data_dir, fetch_models, fetch_file):
        self.data_dir = Path(data_dir)
        self.home = folder(data_dir)
        self.fetch_models, self.fetch_file = fetch_models, fetch_file
        self.task = None

    # State shown on the Subtitles row.
    def _state(self, **value):
        _save(self.home / "state.json", {"at": time.time(), **value})

    def _step(self, name):
        self._state(state="installing", step=name, progress=STEPS[name] - 5)

    def done(self) -> bool:
        return bool(engine(self.data_dir)) and model_ready(self.data_dir)

    def ensure(self):
        """Start setup in the background unless it's done or already running."""
        if builtin() or getattr(sys, "frozen", False) or self.done():
            return None
        if self.task and not self.task.done():
            return self.task
        self.task = asyncio.create_task(self._run())
        return self.task

    async def stop(self):
        """Subtitles was turned off: stop setting up, keep what's installed."""
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        state = _load(self.home / "state.json")
        if state.get("state") == "installing":
            self._state(state="stopped")

    async def _run(self):
        try:
            await self.install()
        except asyncio.CancelledError:
            raise
        except SetupError as exc:
            self._state(state="failed", error=str(exc))
            print(f"Subtitle setup stopped: {exc}", flush=True)
        except Exception as exc:  # The row says so; a repair or restart tries again.
            self._state(state="failed", error="Subtitle setup stopped unexpectedly. Run the setup command again.")
            print(f"Subtitle setup stopped: {type(exc).__name__}: {exc}", flush=True)

    async def install(self):
        self.home.mkdir(parents=True, exist_ok=True)
        venv = self.home / "venv"
        bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
        venv_python = bin_dir / ("python.exe" if os.name == "nt" else "python")
        marker = _load(self.home / "steps.json")

        def finished(step, **extra):
            marker[step] = {"at": time.time(), "setup_version": SETUP_VERSION, **extra}
            _save(self.home / "steps.json", marker)

        def has(step):
            return marker.get(step, {}).get("setup_version") == SETUP_VERSION

        self._step("uv")
        uv = await asyncio.to_thread(self.uv)

        self._step("environment")
        if not (has("environment") and venv_python.is_file()):
            try:
                await asyncio.to_thread(
                    _run, [uv, "venv", "--quiet", "--allow-existing", "--python", sys.executable, str(venv)],
                    env=self.uv_env(),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise SetupError("Couldn't make a Python environment for subtitles.") from exc
            finished("environment")

        self._step("engine")
        if not has("engine"):
            try:
                await asyncio.to_thread(
                    _run,
                    [uv, "pip", "install", "--quiet", "--python", str(venv_python),
                     "--require-hashes", "-r", str(REQUIREMENTS)],
                    env=self.uv_env(),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise SetupError(
                    "Couldn't download the speech engine. Check this computer's internet connection."
                ) from exc
            finished("engine")

        gpu = gpu_name()
        libraries = []
        self._step("gpu")
        if gpu and platform.system() in ("Linux", "Windows"):
            if not has("gpu"):
                try:
                    await asyncio.to_thread(
                        _run,
                        [uv, "pip", "install", "--quiet", "--python", str(venv_python), *GPU_PACKAGES],
                        env=self.uv_env(),
                    )
                    finished("gpu")
                except (OSError, subprocess.SubprocessError):
                    # The processor still works; the row names the card's problem.
                    from .speech_device import remember_failure

                    remember_failure(RuntimeError("NVIDIA's CUDA libraries didn't download."), self.home)
            libraries = await asyncio.to_thread(self.cuda_libraries, venv_python)

        self._step("model")
        await self.model()

        self._step("check")
        cuda = await asyncio.to_thread(self.check, venv_python, libraries, bool(gpu))
        _save(
            self.home / "engine.json",
            {
                "setup_version": SETUP_VERSION,
                "python": str(venv_python),
                "library_paths": libraries,
                "cuda": cuda,
                "gpu": gpu,
                "at": time.time(),
            },
        )
        self._state(state="ready", progress=100)

    # uv: on PATH, left by the setup command, or fetched into the node folder.
    def uv_env(self):
        env = dict(os.environ)
        env.setdefault("UV_CACHE_DIR", str(self.home / "uv-cache"))
        env["UV_PYTHON_INSTALL_DIR"] = str(self.data_dir / "python")
        return env

    def uv(self):
        exe = "uv.exe" if os.name == "nt" else "uv"
        for candidate in (
            shutil.which("uv"),
            self.data_dir / "uv" / exe,
            self.data_dir / "uv" / "bin" / exe,
        ):
            if candidate and Path(candidate).is_file():
                return str(candidate)
        if os.name == "nt" or not shutil.which("curl") or not shutil.which("sh"):
            raise SetupError("Subtitles needs uv on this computer. Run the setup command again.")
        try:
            script = _run(["curl", "-LsSf", "https://astral.sh/uv/install.sh"], timeout=120)
            subprocess.run(
                ["sh"], input=script, text=True, capture_output=True, timeout=600, check=True,
                env={**os.environ, "UV_UNMANAGED_INSTALL": str(self.data_dir / "uv")},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SetupError("Couldn't download uv. Check this computer's internet connection.") from exc
        for candidate in (self.data_dir / "uv" / exe, self.data_dir / "uv" / "bin" / exe):
            if candidate.is_file():
                return str(candidate)
        raise SetupError("Couldn't download uv. Check this computer's internet connection.")

    @staticmethod
    def cuda_libraries(venv_python):
        """Library folders of NVIDIA's pip packages inside the environment."""
        try:
            out = _run(
                [str(venv_python), "-c",
                 "import json,site,glob,os;"
                 "print(json.dumps(sorted({os.path.dirname(p) for s in site.getsitepackages() "
                 "for p in glob.glob(os.path.join(s,'nvidia','*','lib','*.so*'))+"
                 "glob.glob(os.path.join(s,'nvidia','*','bin','*.dll'))})))"],
                timeout=60,
            )
            return json.loads(out.strip().splitlines()[-1])
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            return []

    def check(self, venv_python, libraries, gpu):
        """Prove the engine imports; with a card, prove it loads the model there."""
        from .subtitle_node import evidence_model

        model = evidence_model(self.data_dir)
        env = dict(os.environ)
        if libraries:
            name = "PATH" if os.name == "nt" else "LD_LIBRARY_PATH"
            env[name] = os.pathsep.join([*libraries, *([env[name]] if env.get(name) else [])])
        try:
            _run([str(venv_python), "-c", "import faster_whisper, ctranslate2"], env=env, timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            raise SetupError("The speech engine didn't start. Run the setup command again.") from exc
        if not gpu or not model:
            return False
        probe = (
            "import sys,ctranslate2;"
            "from faster_whisper import WhisperModel;"
            "assert ctranslate2.get_cuda_device_count()>0, 'No CUDA device';"
            "WhisperModel(sys.argv[1],device='cuda',compute_type='int8_float16' "
            "if 'int8_float16' in ctranslate2.get_supported_compute_types('cuda') else 'float32',"
            "local_files_only=True)"
        )
        try:
            _run([str(venv_python), "-c", probe, str(model)], env=env, timeout=600)
            from .speech_device import forget_failure

            forget_failure(self.home)
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            from .speech_device import remember_failure

            detail = (getattr(exc, "stderr", "") or "").strip().splitlines()
            remember_failure(RuntimeError(detail[-1] if detail else "The graphics card didn't load the model."), self.home)
            return False

    async def model(self):
        """Copy the best speech model this server has, resuming and verifying it."""
        from .subtitle_node import evidence_model

        if evidence_model(self.data_dir):
            return
        try:
            listing = await self.fetch_models()
        except Exception as exc:
            raise SetupError("Couldn't get the speech model from Sparrow. Check this computer can reach it.") from exc
        by_name = {m["name"]: m for m in listing if MODEL_NAME.fullmatch(str(m.get("name", "")))}
        choice = next((by_name[n] for n in MODEL_ORDER if n in by_name), None)
        if not choice:
            raise SetupError("This Sparrow server has no speech model to share.")
        target = self.data_dir / "models" / choice["name"]
        partial = self.data_dir / "models" / (choice["name"] + ".part")
        partial.mkdir(parents=True, exist_ok=True)
        files = [f for f in choice["files"] if MODEL_FILE.fullmatch(str(f.get("name", "")))]
        total = sum(int(f["size"]) for f in files) or 1
        copied = 0
        for entry in files:
            path = partial / entry["name"]
            size = int(entry["size"])
            have = path.stat().st_size if path.exists() else 0
            if have > size:
                path.unlink()
                have = 0
            if have < size:
                try:
                    with path.open("ab") as output:
                        async for chunk in self.fetch_file(choice["name"], entry["name"], have):
                            have += len(chunk)
                            if have > size:
                                break
                            await asyncio.to_thread(output.write, chunk)
                            progress = STEPS["gpu"] + (STEPS["model"] - STEPS["gpu"]) * (copied + have) / total
                            self._state(state="installing", step="model", progress=round(progress))
                except Exception as exc:
                    raise SetupError("The speech model download was interrupted. It resumes on the next try.") from exc
            if path.stat().st_size != size or await asyncio.to_thread(_sha256, path) != entry["sha256"]:
                path.unlink(missing_ok=True)
                raise SetupError("The speech model didn't arrive intact. It downloads again on the next try.")
            copied += size
        if target.exists():
            shutil.rmtree(target)
        os.replace(partial, target)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# ─── This server sharing its models ─────────────────────────────────


_hashes = {}


def shared_models(data_dir):
    """Speech models this server can give a paired computer: name, files, sizes, hashes."""
    from .subtitle_node import _model_folders, model_path

    out = []
    for name in MODEL_ORDER:
        folders = [*_model_folders(data_dir, name)]
        if name == "whisper-base":
            folders.insert(0, model_path(data_dir))
        found = next((f for f in folders if (Path(f) / "model.bin").is_file()), None)
        if not found:
            continue
        files = []
        for path in sorted(Path(found).iterdir()):
            if not path.is_file() or not MODEL_FILE.fullmatch(path.name):
                continue
            stat = path.stat()
            key = (str(path), stat.st_size, stat.st_mtime_ns)
            if key not in _hashes:
                _hashes[key] = _sha256(path)
            files.append({"name": path.name, "size": stat.st_size, "sha256": _hashes[key]})
        out.append({"name": name, "files": files, "path": str(found)})
    return out


def shared_file(data_dir, name, file):
    """The path of one shared model file, or None."""
    if not MODEL_NAME.fullmatch(name or "") or not MODEL_FILE.fullmatch(file or ""):
        return None
    for model in shared_models_paths(data_dir):
        if model[0] == name:
            path = Path(model[1]) / file
            if path.is_file() and path.parent == Path(model[1]):
                return path
    return None


def shared_models_paths(data_dir):
    from .subtitle_node import _model_folders, model_path

    for name in MODEL_ORDER:
        folders = [*_model_folders(data_dir, name)]
        if name == "whisper-base":
            folders.insert(0, model_path(data_dir))
        found = next((f for f in folders if (Path(f) / "model.bin").is_file()), None)
        if found:
            yield name, str(found)

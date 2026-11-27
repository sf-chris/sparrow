"""Subtitles on any computer: the graphics card when it works, else the processor.

Covers choosing the device (with a fake CUDA library: the office has no
working card), the computer's speech report, a paired computer setting the
engine up by itself, the server sharing its models, choosing the listening
computer, and real evidence built from another computer's decoded audio.
"""

import asyncio
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.agents import computers, speech_device, speech_setup
from backend.agents.node_executor import Executor, executable, file_version

ROOT = Path(__file__).resolve().parents[1]
MODEL_ENV = ("SPARROW_TRANSCRIPTION_MODEL", "SPARROW_EVIDENCE_MODEL", "SPARROW_DATA_DIR", "SPARROW_SPEECH_DEVICE")


def blocks(text):
    """(name, requirement block) for a pip requirements file with hashes."""
    out = []
    for block in re.split(r"\n(?=[a-zA-Z])", text):
        match = re.match(r"([A-Za-z0-9_.-]+)==", block)
        if match:
            out.append((match.group(1).lower(), block.split("\n    # via")[0].rstrip()))
    return out


class RequirementsTests(unittest.TestCase):
    def test_the_speech_engine_is_pinned_exactly_as_the_server_installs_it(self):
        lock = (ROOT / "requirements.lock").read_text()
        wanted = blocks(speech_setup.REQUIREMENTS.read_text())
        names = {name for name, _ in wanted}
        self.assertTrue({"faster-whisper", "ctranslate2", "onnxruntime", "av", "tokenizers"} <= names)
        for name, block in wanted:
            self.assertIn(block, lock, f"{name} drifted from requirements.lock")
            self.assertIn("--hash=sha256:", block)
        # Nothing the engine needs is left out: every package the lock says is
        # needed by one of these is here too.
        for block in re.split(r"\n(?=[a-zA-Z])", lock):
            match = re.match(r"([A-Za-z0-9_.-]+)==", block)
            via = re.findall(r"#\s+(?:via\s+)?([a-z0-9_.-]+)\s*$", block, re.M)
            if match and any(v in names for v in via):
                self.assertIn(match.group(1).lower(), names)


class FakeCTranslate2:
    def __init__(self, cuda=1, kinds=("float16", "int8_float16", "int8", "float32")):
        self.cuda, self.kinds, self.asked = cuda, kinds, 0

    def get_cuda_device_count(self):
        self.asked += 1
        return self.cuda

    def get_supported_compute_types(self, device):
        return set(self.kinds) if device == "cuda" else {"int8", "float32"}


class FakeModel:
    loads = []
    broken_card = None

    def __init__(self, path, *, device, compute_type, **_):
        FakeModel.loads.append((device, compute_type))
        if device == "cuda" and FakeModel.broken_card:
            raise RuntimeError(FakeModel.broken_card)
        self.device = device


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        FakeModel.loads, FakeModel.broken_card = [], None
        self.env = mock.patch.dict(os.environ, {"SPARROW_SPEECH_DEVICE": "auto"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def load(self, cuda=FakeCTranslate2()):
        with mock.patch.dict(sys.modules, {"ctranslate2": cuda}):
            return speech_device.load("model", 4, folder=self.state, factory=FakeModel)

    def test_a_graphics_card_is_used_in_its_fastest_format(self):
        model, compute = self.load()
        self.assertEqual((model.device, compute), ("cuda", "cuda float16"))
        model, compute = self.load(FakeCTranslate2(kinds=("int8", "float32")))
        self.assertEqual(compute, "cuda int8")

    def test_no_card_means_the_processor(self):
        model, compute = self.load(FakeCTranslate2(cuda=0))
        self.assertEqual((model.device, compute), ("cpu", "cpu int8"))

    def test_a_card_that_fails_is_set_aside_and_the_work_carries_on(self):
        FakeModel.broken_card = "Library libcublas.so.12 is not found or cannot be loaded"
        model, compute = self.load()
        self.assertEqual(compute, "cpu int8")
        self.assertIn("libcublas", speech_device.failure(self.state)["reason"])
        # It isn't tried again while the failure is remembered.
        FakeModel.loads = []
        self.load()
        self.assertEqual([device for device, _ in FakeModel.loads], ["cpu"])
        # A week later it's tried again.
        record = json.loads((self.state / speech_device.FAILURE).read_text())
        record["at"] = time.time() - speech_device.RETRY_SECONDS - 1
        (self.state / speech_device.FAILURE).write_text(json.dumps(record))
        self.assertIsNone(speech_device.failure(self.state))

    def test_a_card_failing_mid_run_moves_the_work_to_the_processor(self):
        with mock.patch.dict(sys.modules, {"ctranslate2": FakeCTranslate2()}):
            recogniser = speech_device.Recogniser("model", 4, folder=self.state, factory=FakeModel)

            def work(model):
                if model.device == "cuda":
                    raise RuntimeError("CUDA failed with error out of memory")
                return "heard"

            self.assertEqual(recogniser.run(work), "heard")
        self.assertEqual(recogniser.compute, "cpu int8")
        self.assertIn("out of memory", recogniser.fallback)
        self.assertIsNotNone(speech_device.failure(self.state))

    def test_other_errors_are_never_hidden_as_card_failures(self):
        with mock.patch.dict(sys.modules, {"ctranslate2": FakeCTranslate2()}):
            recogniser = speech_device.Recogniser("model", 4, folder=self.state, factory=FakeModel)
            with self.assertRaises(ValueError):
                recogniser.run(lambda model: (_ for _ in ()).throw(ValueError("bad audio")))
        self.assertIsNone(speech_device.failure(self.state))

    def test_the_processor_can_be_forced(self):
        with mock.patch.dict(os.environ, {"SPARROW_SPEECH_DEVICE": "cpu"}):
            _, compute = self.load()
        self.assertEqual(compute, "cpu int8")


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        speech_setup._status_cache.clear()
        self.patches = [
            mock.patch.dict(os.environ, {k: "" for k in MODEL_ENV}),
            mock.patch.object(speech_setup, "gpu_name", return_value="NVIDIA GeForce RTX 4080"),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        speech_setup._status_cache.clear()
        self.temp.cleanup()

    def node(self, speech, node_id="gamer"):
        return {"id": node_id, "online": True, "disabled": False, "capabilities": {"speech": speech}}

    def test_this_server_listens_with_its_own_engine_on_the_card(self):
        with mock.patch.object(speech_setup, "builtin", return_value=True), \
                mock.patch.object(speech_setup, "_builtin_cuda", return_value=True), \
                mock.patch.object(speech_setup, "model_ready", return_value=True):
            report = speech_setup.status(self.data)
        self.assertEqual(report, {"ready": True, "device": "cuda", "gpu": "NVIDIA GeForce RTX 4080", "engine": "builtin"})
        row = computers.health(self.node(report, "local"), {"subtitles": True})["rows"][-1]
        self.assertEqual((row["state"], row["line"]), ("ready", "Uses the NVIDIA GeForce RTX 4080"))

    def test_a_paired_computer_reports_setting_up_then_ready(self):
        with mock.patch.object(speech_setup, "builtin", return_value=False):
            speech_setup._save(self.data / "speech" / "state.json", {"state": "installing", "progress": 40})
            report = speech_setup.status(self.data)
            self.assertEqual((report["ready"], report["installing"], report["progress"]), (False, True, 40))
            row = computers.health(self.node(report), {"subtitles": True})["rows"][-1]
            self.assertEqual((row["state"], row["line"]), ("waiting", "Setting up. 40%"))

            speech_setup._save(self.data / "speech" / "state.json", {"state": "failed", "error": "Couldn't download the speech engine."})
            speech_setup._status_cache.clear()
            report = speech_setup.status(self.data)
            row = computers.health(self.node(report), {"subtitles": True})["rows"][-1]
            self.assertEqual((row["state"], row["fix"]), ("attention", "repair"))

            python = self.data / "speech" / "venv" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("")
            speech_setup._save(
                self.data / "speech" / "engine.json",
                {"setup_version": speech_setup.SETUP_VERSION, "python": str(python), "cuda": True},
            )
            speech_setup._status_cache.clear()
            with mock.patch.object(speech_setup, "model_ready", return_value=True):
                report = speech_setup.status(self.data)
            self.assertEqual((report["ready"], report["device"], report["engine"]), (True, "cuda", "private"))
            self.assertEqual(speech_setup.python(self.data), str(python))

    def test_a_card_set_aside_is_named_and_the_processor_is_used(self):
        speech_device.remember_failure(RuntimeError("no kernel image is available"), self.data / "speech")
        with mock.patch.object(speech_setup, "builtin", return_value=True), \
                mock.patch.object(speech_setup, "_builtin_cuda", return_value=True), \
                mock.patch.object(speech_setup, "model_ready", return_value=True):
            report = speech_setup.status(self.data)
        self.assertEqual(report["device"], "cpu")
        self.assertIn("kernel image", report["gpu_problem"])

    def test_executor_reports_speech_in_its_heartbeat(self):
        executor = Executor(self.data / "node", {})
        speech = executor.capabilities()["speech"]
        self.assertIn("ready", speech)
        self.assertIn(speech["device"], ("cpu", "cuda"))


FAKE_UV = """#!{python}
import json, os, sys
log = os.environ["FAKE_UV_LOG"]
with open(log, "a") as out:
    out.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] == "venv":
    target = sys.argv[-1]
    os.makedirs(os.path.join(target, "bin"), exist_ok=True)
    python = os.path.join(target, "bin", "python")
    with open(python, "w") as wrapper:
        wrapper.write("#!/bin/sh\\nexec {python} \\"$@\\"\\n")
    os.chmod(python, 0o755)
if sys.argv[1] == "pip" and os.environ.get("FAKE_UV_FAIL") == "pip":
    sys.exit(2)
"""


class NodeSetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.data = root / "node"
        self.data.mkdir()
        self.log = root / "uv.log"
        uv = root / "bin" / "uv"
        uv.parent.mkdir()
        uv.write_text(FAKE_UV.replace("{python}", sys.executable))
        uv.chmod(0o755)
        # The server's model: two small files standing in for a real one.
        self.model = root / "server-models" / "whisper-medium"
        self.model.mkdir(parents=True)
        (self.model / "model.bin").write_bytes(os.urandom(3 * 1024 * 1024 + 17))
        (self.model / "config.json").write_text("{}")
        self.listing = [
            {
                "name": "whisper-medium",
                "files": [
                    {"name": p.name, "size": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                    for p in sorted(self.model.iterdir())
                ],
            }
        ]
        self.offsets, self.interrupt = [], False
        self.patches = [
            mock.patch.dict(os.environ, {**{k: "" for k in MODEL_ENV}, "FAKE_UV_LOG": str(self.log), "PATH": f"{uv.parent}:{os.environ['PATH']}"}),
            mock.patch.object(speech_setup, "builtin", return_value=False),
            mock.patch.object(speech_setup, "gpu_name", return_value=None),
        ]
        for patch in self.patches:
            patch.start()
        speech_setup._status_cache.clear()

    async def asyncTearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        speech_setup._status_cache.clear()
        self.temp.cleanup()

    async def models(self):
        return self.listing

    async def model_file(self, name, file, offset):
        self.offsets.append((file, offset))
        data = (self.model / file).read_bytes()[offset:]
        for start in range(0, len(data), 1024 * 1024):
            if self.interrupt and start:
                self.interrupt = False
                raise ConnectionError("dropped")
            yield data[start:start + 1024 * 1024]

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    async def test_setup_installs_the_pinned_engine_and_the_servers_model_once(self):
        speech = speech_setup.NodeSpeech(self.data, self.models, self.model_file)
        await speech.install()
        calls = self.calls()
        self.assertEqual([c[0] for c in calls], ["venv", "pip"])
        self.assertIn("--require-hashes", calls[1])
        self.assertIn(str(speech_setup.REQUIREMENTS), calls[1])
        installed = self.data / "models" / "whisper-medium"
        self.assertEqual((installed / "model.bin").read_bytes(), (self.model / "model.bin").read_bytes())
        self.assertTrue(speech.done())
        report = speech_setup.status(self.data)
        self.assertEqual((report["ready"], report["device"], report["engine"]), (True, "cpu", "private"))
        # The worker runs with the private Python and finds Sparrow's code.
        self.assertTrue(speech_setup.python(self.data).endswith("venv/bin/python"))
        env = speech_setup.environment(self.data)
        self.assertIn(str(speech_setup.APP_ROOT), env["PYTHONPATH"])
        self.assertEqual(env["SPARROW_SPEECH_STATE"], str(self.data / "speech"))
        # A repair run does nothing that's already done.
        await speech.install()
        self.assertEqual(len(self.calls()), 2)

    async def test_an_interrupted_model_download_resumes_where_it_stopped(self):
        speech = speech_setup.NodeSpeech(self.data, self.models, self.model_file)
        self.interrupt = True
        with self.assertRaises(speech_setup.SetupError):
            await speech.install()
        await speech.install()
        resumed = [offset for file, offset in self.offsets if file == "model.bin"]
        self.assertEqual(resumed[0], 0)
        self.assertEqual(resumed[-1], 1024 * 1024)
        self.assertTrue(speech.done())

    async def test_a_model_that_arrives_damaged_is_fetched_again(self):
        self.listing[0]["files"][-1]["sha256"] = "0" * 64
        speech = speech_setup.NodeSpeech(self.data, self.models, self.model_file)
        with self.assertRaisesRegex(speech_setup.SetupError, "intact"):
            await speech.install()
        self.assertFalse((self.data / "models" / "whisper-medium.part" / "model.bin").exists())

    async def test_a_failed_download_is_shown_on_the_subtitles_row(self):
        speech = speech_setup.NodeSpeech(self.data, self.models, self.model_file)
        with mock.patch.dict(os.environ, {"FAKE_UV_FAIL": "pip"}):
            await speech.ensure()
        report = speech_setup.status(self.data)
        self.assertIn("speech engine", report["error"])
        self.assertFalse(report["ready"])

    async def test_a_graphics_card_gets_cuda_libraries_and_is_proved_before_use(self):
        with mock.patch.object(speech_setup, "gpu_name", return_value="NVIDIA GeForce RTX 3060"):
            speech = speech_setup.NodeSpeech(self.data, self.models, self.model_file)
            await speech.install()
            pip = [c for c in self.calls() if c[0] == "pip"]
            self.assertEqual(len(pip), 2)
            self.assertTrue(set(speech_setup.GPU_PACKAGES) <= set(pip[1]))
            # This office has no working card, so proving it fails: the
            # processor is used and the card's problem is recorded.
            engine = speech_setup.engine(self.data)
            self.assertFalse(engine["cuda"])
            speech_setup._status_cache.clear()
            report = speech_setup.status(self.data)
            self.assertEqual((report["ready"], report["device"], report["gpu"]), (True, "cpu", "NVIDIA GeForce RTX 3060"))
            self.assertTrue(report["gpu_problem"])

    async def test_turning_subtitles_off_stops_setup_and_keeps_what_was_done(self):
        started = asyncio.Event()

        async def slow_models():
            started.set()
            await asyncio.sleep(30)
            return self.listing

        speech = speech_setup.NodeSpeech(self.data, slow_models, self.model_file)
        task = speech.ensure()
        await asyncio.wait_for(started.wait(), 20)
        await speech.stop()
        self.assertTrue(task.cancelled() or task.done())
        state = json.loads((self.data / "speech" / "state.json").read_text())
        self.assertEqual(state["state"], "stopped")
        self.assertTrue((self.data / "speech" / "venv" / "bin" / "python").exists())

    @unittest.skipUnless(os.getenv("SPARROW_REAL_SPEECH_SETUP"), "set SPARROW_REAL_SPEECH_SETUP=1 to install the real engine")
    async def test_the_real_engine_installs_with_uv(self):
        for patch in self.patches[:1]:
            patch.stop()
        self.patches = self.patches[1:]
        with mock.patch.dict(os.environ, {k: "" for k in MODEL_ENV}):
            speech = speech_setup.NodeSpeech(self.data, self.models, self.model_file)
            await speech.install()
            out = speech_setup._run(
                [speech_setup.python(self.data), "-c", "import faster_whisper, ctranslate2; print(ctranslate2.__version__)"]
            )
        self.assertTrue(out.strip())


class SharedModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.agents.nodes import install_nodes
        from backend.models import SparrowConfig
        from backend.storage import Storage

        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        model = root / "models" / "whisper-base"
        model.mkdir(parents=True)
        self.weights = os.urandom(2 * 1024 * 1024 + 5)
        (model / "model.bin").write_bytes(self.weights)
        (model / "tokenizer.json").write_text("{}")
        self.env = mock.patch.dict(os.environ, {**{k: "" for k in MODEL_ENV}, "SPARROW_TRANSCRIPTION_MODEL": str(model)})
        self.env.start()
        storage = Storage(str(root / "server"))
        await storage.load_all()
        await storage.save_config(SparrowConfig())
        app = FastAPI()
        self.nodes = install_nodes(app, storage, lambda: None)
        self.client = TestClient(app)
        code = self.nodes.enroll()["code"]
        self.credential = secrets.token_urlsafe(32)
        self.nodes.pair(code, self.credential, Executor(root / "node", {}).capabilities())

    async def asyncTearDown(self):
        self.env.stop()
        self.temp.cleanup()

    async def test_a_paired_computer_can_fetch_this_servers_model_and_nothing_else(self):
        auth = {"Authorization": "Bearer " + self.credential}
        self.assertEqual(self.client.get("/api/v1/node/models").status_code, 401)
        listing = self.client.get("/api/v1/node/models", headers=auth).json()
        self.assertEqual([m["name"] for m in listing], ["whisper-base"])
        weights = next(f for f in listing[0]["files"] if f["name"] == "model.bin")
        self.assertEqual(weights["sha256"], hashlib.sha256(self.weights).hexdigest())
        self.assertNotIn("path", listing[0])
        part = self.client.get(
            "/api/v1/node/models/whisper-base/model.bin", params={"offset": 1024}, headers=auth
        )
        self.assertEqual(part.content, self.weights[1024:])
        for name, file in (("whisper-base", "..%2Fsecret"), ("../etc", "passwd"), ("whisper-large", "model.bin")):
            self.assertEqual(
                self.client.get(f"/api/v1/node/models/{name}/{file}", headers=auth).status_code, 404
            )


class FakeNodes:
    """Computers for choosing who listens, with recorded operations."""

    def __init__(self, computers, able=(), on=()):
        self.computers, self.ready, self.on = computers, set(able), set(on) | set(able)
        self.calls, self.transfers, self.closed = [], [], []

    def list(self):
        return self.computers

    def info(self, node_id):
        return next((c for c in self.computers if c["id"] == node_id), None)

    def able(self, node_id, job):
        return node_id in self.ready

    def jobs(self, node_id, capabilities=None):
        return {"subtitles": node_id in self.on}

    def open_transfer(self, token, target, source, root_id, path, version):
        self.transfers.append((token, target, source, root_id, path))
        return token

    def close_transfers(self, prefix):
        self.closed.append(prefix)

    async def execute(self, node_id, kind, args, **_):
        self.calls.append((node_id, kind, args))
        if kind == "subtitle_audio":
            return {
                "path": f"subtitle-audio/{'b' * 32}/audio.wav",
                "version": {"size_bytes": 64, "mtime_ns": 1},
                "sha256": "c" * 64,
                "size_bytes": 64,
                "audio": {"audio_index": 1, "audio_seconds": 24.0},
            }
        return {"state": "complete", "progress": 1, "path": "subtitle-evidence/x/evidence.json",
                "version": {"size_bytes": 1}, "model": "fixture", "coverage": {}}


def computer(node_id, device="cpu"):
    return {"id": node_id, "name": node_id.title(), "disabled": False,
            "capabilities": {"speech": {"ready": True, "device": device}}}


class ListenerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from backend.agents.accounts import Accounts
        from backend.agents.subtitles import Subtitles

        self.temp = tempfile.TemporaryDirectory()
        self.Subtitles, self.accounts = Subtitles, Accounts(self.temp.name)

    async def asyncTearDown(self):
        self.temp.cleanup()

    def subtitles(self, nodes):
        manager = self.Subtitles(self.accounts, nodes, None, lambda: None)
        manager.update = lambda *a, **k: None
        manager.authority = lambda task: None
        return manager

    def test_the_best_computer_listens(self):
        asset = {"node_id": "nas"}
        everyone = [computer("local"), computer("nas"), computer("gamer", "cuda")]
        # A graphics card first, wherever the file is.
        manager = self.subtitles(FakeNodes(everyone, able={"local", "nas", "gamer"}))
        self.assertEqual(manager.listener(asset), ("gamer", ""))
        # Then the computer holding the file, then this server.
        manager = self.subtitles(FakeNodes(everyone, able={"local", "nas"}))
        self.assertEqual(manager.listener(asset)[0], "nas")
        manager = self.subtitles(FakeNodes(everyone, able={"local"}))
        self.assertEqual(manager.listener(asset)[0], "local")
        # None ready: the one that should be is asked, so it says why.
        manager = self.subtitles(FakeNodes(everyone, on={"gamer"}))
        self.assertEqual(manager.listener(asset)[0], "gamer")
        # Nobody does subtitles: say so plainly.
        node, reason = self.subtitles(FakeNodes(everyone)).listener(asset)
        self.assertIsNone(node)
        self.assertIn("Turn on Subtitles", reason)

    async def test_another_computer_listens_to_audio_decoded_where_the_file_is(self):
        nodes = FakeNodes([computer("nas"), computer("gamer", "cuda")], able={"nas", "gamer"})
        manager = self.subtitles(nodes)
        task = {"id": "t" * 16, "data": {"version": {"size_bytes": 9}, "audio_index": 1}}
        asset = {"node_id": "nas", "root_id": "library", "path": "Show/S01E01.mkv"}
        result = await manager.evidence(task, asset)
        self.assertEqual(result["node"], "gamer")
        (holder, first, copy_args), (listener, second, args) = nodes.calls
        self.assertEqual((holder, first, listener, second), ("nas", "subtitle_audio", "gamer", "subtitle_evidence"))
        self.assertEqual(copy_args["path"], "Show/S01E01.mkv")
        # The listener has no copy of the video: it fetches the decoded audio.
        self.assertEqual((args["root_id"], args["path"]), ("cache", ""))
        self.assertEqual(args["version"], task["data"]["version"])
        token, target, source, root_id, path = nodes.transfers[0]
        self.assertEqual((target, source, root_id, args["remote"]["source"]), ("gamer", "nas", "cache", token))
        self.assertEqual(nodes.closed[-1], token)

    async def test_the_computer_holding_the_file_listens_without_copying(self):
        nodes = FakeNodes([computer("local")], able={"local"})
        manager = self.subtitles(nodes)
        task = {"id": "t" * 16, "data": {"version": {"size_bytes": 9}, "audio_index": 1}}
        result = await manager.evidence(task, {"node_id": "local", "root_id": "library", "path": "a.mkv"})
        self.assertEqual(result["node"], "local")
        self.assertEqual([kind for _, kind, _ in nodes.calls], ["subtitle_evidence"])
        self.assertNotIn("remote", nodes.calls[0][2])


@unittest.skipUnless(executable("ffmpeg") and executable("ffprobe"), "requires media tools")
class RemoteEvidenceTests(unittest.IsolatedAsyncioTestCase):
    """Real speech: evidence built from another computer's audio matches local evidence."""

    async def test_evidence_from_a_decoded_copy_matches_evidence_made_beside_the_file(self):
        from backend.agents.node_executor import run_media
        from backend.agents.subtitle_node import evidence_model

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if not evidence_model(root / "storage"):
                self.skipTest("requires an installed speech model")
            library = root / "library"
            library.mkdir()
            clips = sorted((Path(__file__).parent / "fixtures/subtitles").glob("*.flac"))[:2]
            inputs = [arg for clip in clips for arg in ("-i", str(clip))]
            parts = "".join(f"[{n + 1}:a]" for n in range(len(clips)))
            await run_media(
                "ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=2", *inputs,
                "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=30",
                "-filter_complex", "[0:a]" + parts + f"concat=n={len(clips) + 1}:v=0:a=1[a]",
                "-map", f"{len(clips) + 1}:v", "-map", "[a]", "-shortest",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                "-metadata:s:a:0", "language=eng", "-y", str(library / "speech.mkv"),
                timeout=120,
            )
            media = library / "speech.mkv"
            storage = Executor(root / "storage", {"library": str(library)})

            async def fetch(token, offset):
                # What Nodes.transfer_bytes does: read the copy from the storage computer.
                path = storage.cache_root / token
                with path.open("rb") as source:
                    source.seek(offset)
                    while chunk := source.read(256 * 1024):
                        yield chunk

            listener = Executor(root / "listener", {}, fetcher=fetch)

            def command(kind, args):
                return {"id": secrets.token_hex(12), "kind": kind, "args": args, "expires": time.time() + 900}

            base = {"version": file_version(media), "audio_index": 1, "budget": 60}
            copy = await storage.execute(command("subtitle_audio", {"root_id": "library", "path": "speech.mkv", **base}))
            self.assertTrue(copy["ok"], copy)
            copy = copy["value"]
            self.assertLess(copy["size_bytes"], 2 * 1024 * 1024)
            remote = {"source": copy["path"], "size_bytes": copy["size_bytes"], "sha256": copy["sha256"], "audio": copy["audio"]}

            async def until_complete(executor, args):
                result = await executor.execute(command("subtitle_evidence", args))
                while result["ok"] and result["value"]["state"] != "complete":
                    result = await executor.execute(command("subtitle_evidence", args))
                self.assertTrue(result["ok"], result)
                return result["value"]

            heard = await until_complete(listener, {"root_id": "cache", "path": "", **base, "remote": remote})
            local = await until_complete(storage, {"root_id": "library", "path": "speech.mkv", **base})
            # Keyed to the video's version: the same evidence, wherever it was made.
            self.assertEqual(heard["path"], local["path"])
            there = json.loads((listener.cache_root / heard["path"]).read_text())
            here = json.loads((storage.cache_root / local["path"]).read_text())
            self.assertEqual(there["media_version"], here["media_version"])
            self.assertEqual(
                [(u["text"], u["onset"]) for u in there["utterances"]],
                [(u["text"], u["onset"]) for u in here["utterances"]],
            )
            self.assertGreater(there["utterances"][0]["onset"], 2.0)
            self.assertIn(there["recogniser"]["compute"], ("cpu int8", "cuda float16", "cuda int8_float16"))
            # Re-listening happens on the computer holding the evidence.
            key = heard["path"].split("/")[1]
            listen = await listener.execute(command(
                "subtitle_listen",
                {"root_id": "cache", "path": "", "evidence": key, "start": 1.5, "end": 8.0},
            ))
            self.assertTrue(listen["ok"], listen)
            self.assertTrue(listen["value"]["utterances"])
            # A damaged copy is refused and fetched again.
            bad = await Executor(root / "other", {}, fetcher=fetch).execute(command(
                "subtitle_evidence",
                {"root_id": "cache", "path": "", **base, "remote": {**remote, "sha256": "0" * 64}},
            ))
            self.assertFalse(bad["ok"])
            self.assertIn("intact", bad["error"])


if __name__ == "__main__":
    unittest.main()

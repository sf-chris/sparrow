import json
import sys
from pathlib import Path
from huggingface_hub import snapshot_download

config = json.loads((Path(__file__).parent / "sources.json").read_text())["whisper"]
snapshot_download(
    config["repository"],
    revision=config["revision"],
    local_dir=sys.argv[1],
    allow_patterns=[
        "config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.txt",
        "README.md",
    ],
)

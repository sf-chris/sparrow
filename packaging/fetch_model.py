import json
import sys
from pathlib import Path
from huggingface_hub import snapshot_download

# fetch_model.py DESTINATION [whisper|whisper_evidence]
source = sys.argv[2] if len(sys.argv) > 2 else "whisper"
config = json.loads((Path(__file__).parent / "sources.json").read_text())[source]
snapshot_download(
    config["repository"],
    revision=config["revision"],
    local_dir=sys.argv[1],
    allow_patterns=[
        "config.json",
        "model.bin",
        "tokenizer.json",
        "preprocessor_config.json",
        "vocabulary.txt",
        "vocabulary.json",
        "README.md",
    ],
)

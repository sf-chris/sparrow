import importlib.metadata
import json
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
inventory = []
for dist in importlib.metadata.distributions():
    name = dist.metadata.get("Name", "unknown")
    folder = root / name
    folder.mkdir(exist_ok=True)
    for entry in dist.files or []:
        if any(
            part.lower().startswith(("license", "copying", "notice", "authors"))
            for part in entry.parts
        ):
            source = Path(dist.locate_file(entry))
            if source.is_file():
                shutil.copyfile(source, folder / entry.name)
    inventory.append(
        {
            "name": name,
            "version": dist.version,
            "license": dist.metadata.get("License-Expression")
            or dist.metadata.get("License", ""),
        }
    )
(root / "python-components.json").write_text(json.dumps(inventory, indent=2))

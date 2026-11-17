"""Explicit development-only fetch of pinned upstream SVGs; never run by the app."""

import hashlib
import io
import json
import tarfile
from pathlib import Path
from urllib.request import Request, urlopen

VERSION = "v2.2.0"
SOURCE = f"https://codeload.github.com/tailwindlabs/heroicons/tar.gz/refs/tags/{VERSION}"
TARGET = Path(__file__).resolve().parents[1] / "src" / "cdw" / "assets" / "heroicons"
VARIANTS = ("outline", "solid")
NAMES = (
    "home", "circle-stack", "cpu-chip", "calendar-days", "queue-list", "cog-6-tooth", "user-circle",
    "command-line", "table-cells", "play", "stop", "plus", "pencil-square", "trash", "x-mark",
    "folder-open", "document-text", "arrow-path", "arrow-uturn-left", "sun", "moon",
    "check", "check-circle", "exclamation-triangle", "information-circle", "chevron-down",
    "minus", "arrows-pointing-out", "square-2-stack",
)
TIMEOUT_SECONDS = 60
USER_AGENT = "CDW-asset-vendor/1.0"


def main():
    request = Request(SOURCE, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        archive = response.read()
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        root = bundle.getnames()[0].split("/")[0]
        for variant in VARIANTS:
            for name in NAMES:
                member = bundle.extractfile(f"{root}/optimized/24/{variant}/{name}.svg")
                files[f"{variant}/{name}.svg"] = member.read()
        files["LICENSE"] = bundle.extractfile(f"{root}/LICENSE").read()
    for relative, data in files.items():
        path = TARGET / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {"project": "tailwindlabs/heroicons", "version": VERSION, "source": SOURCE,
                "archive_sha256": hashlib.sha256(archive).hexdigest(),
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    (TARGET / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Vendored {len(files) - 1} SVGs and upstream license from {VERSION}")


if __name__ == "__main__":
    main()

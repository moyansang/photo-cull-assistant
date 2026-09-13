"""Download the pinned OpenCV Zoo body models into an ignored build cache."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from urllib.request import Request, urlopen


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _valid(path: Path, entry: dict) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == entry["size"]
        and _digest(path) == entry["sha256"]
    )


def download(destination: Path) -> list[Path]:
    manifest = json.loads((HERE / "manifest.json").read_text("utf-8"))
    commit = manifest["source_commit"]
    destination.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []
    for entry in manifest["models"]:
        target = destination / entry["filename"]
        if _valid(target, entry):
            downloaded.append(target)
            continue
        url = (
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
            f"{commit}/{entry['relative_source']}"
        )
        request = Request(url, headers={"User-Agent": "photo-cull-assistant-model-fetcher/1"})
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as tmp:
            temporary = Path(tmp.name)
            with urlopen(request, timeout=120) as response:
                shutil.copyfileobj(response, tmp)
        try:
            if not _valid(temporary, entry):
                raise RuntimeError(f"模型校验失败：{entry['filename']}")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        downloaded.append(target)
    return downloaded


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--destination",
        type=Path,
        default=REPO_ROOT / "build" / "body_models",
        help="model output directory (default: build/body_models)",
    )
    args = parser.parse_args()
    for path in download(args.destination.resolve()):
        print(path)


if __name__ == "__main__":
    main()

"""Build the clean Linux amd64 Python 3.14 runtime bundle."""

import argparse
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUNDLE_NAME = "edge-tts-server-python314-linux-amd64"
ALLOWED_TOP_LEVEL = {"libs", "config.example.yaml", "run.py", "LICENSE"}


def _install_runtime(libs: Path) -> None:
    """Install the project and all runtime dependencies into the vendored tree."""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--no-compile",
            "--target",
            str(libs),
            str(ROOT),
        ],
        check=True,
    )


def _remove_forbidden(root: Path) -> None:
    """Remove non-server packages and forbidden generated/documentation files."""
    playback = root / "edge_playback"
    if playback.exists():
        shutil.rmtree(playback)
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if not path.exists():
            continue
        if path.is_dir() and path.name == "__pycache__":
            shutil.rmtree(path)
        elif path.suffix.lower() == ".md":
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        elif path.is_file() and path.suffix.lower() == ".pyc":
            path.unlink()


def _audit(bundle: Path) -> None:
    """Reject anything outside the deliberately small runtime contract."""
    names = {path.name for path in bundle.iterdir()}
    if names != ALLOWED_TOP_LEVEL:
        raise RuntimeError(f"Unexpected bundle root files: {sorted(names)}")
    forbidden = [
        path
        for path in bundle.rglob("*")
        if path.name == "__pycache__"
        or path.suffix.lower() == ".md"
        or (path.is_file() and path.suffix.lower() == ".pyc")
    ]
    if forbidden:
        raise RuntimeError(f"Forbidden bundle files: {forbidden}")


def build_bundle(output_root: Path) -> Path:
    """Build and return the path to the clean runtime archive."""
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    bundle = output_root / BUNDLE_NAME
    archive = output_root / f"{BUNDLE_NAME}.tar.gz"
    if bundle.exists():
        shutil.rmtree(bundle)
    archive.unlink(missing_ok=True)
    libs = bundle / "libs"
    libs.mkdir(parents=True)
    _install_runtime(libs)
    _remove_forbidden(libs)
    shutil.copy2(ROOT / "packaging/python/run.py", bundle / "run.py")
    shutil.copy2(ROOT / "config.example.yaml", bundle / "config.example.yaml")
    shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE")
    _audit(bundle)
    with tarfile.open(archive, "w:gz") as packaged:
        packaged.add(bundle, arcname=BUNDLE_NAME)
    return archive


def main() -> None:
    """Build a runtime bundle into the requested output directory."""
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(build_bundle(args.output))


if __name__ == "__main__":
    main()

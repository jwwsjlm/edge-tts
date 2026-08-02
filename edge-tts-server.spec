"""PyInstaller definition for the standalone HTTP server."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH)

analysis = Analysis(
    [str(ROOT / "src" / "edge_tts_server" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=(
        collect_submodules("edge_tts_server")
        + collect_submodules("fastapi")
        + collect_submodules("pydantic")
        + collect_submodules("uvicorn")
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "_pytest",
        "astroid",
        "black",
        "mypy",
        "pkg_resources",
        "pylint",
        "pytest",
        "setuptools",
    ],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="edge-tts-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
)

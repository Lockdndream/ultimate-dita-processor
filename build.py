"""
build.py  —  DITA Converter Windows EXE builder
================================================
Produces:  dist/DITA-Converter.exe  (single self-contained file)

Usage:
    python build.py          # release build (no console window)
    python build.py --debug  # keep console window for troubleshooting

Requires Python 3.11 and PyInstaller >= 6.0 (installed automatically).
"""
from __future__ import annotations

import subprocess
import sys
import shutil
import argparse
from pathlib import Path

ROOT = Path(__file__).parent

# PyInstaller's socket/C-extension bundling is broken for Python 3.13+ in frozen
# Streamlit builds. Enforce 3.11 or 3.12 at build time.
if sys.version_info >= (3, 13):
    sys.exit(
        f"ERROR: Build requires Python 3.11 or 3.12 "
        f"(running {sys.version.split()[0]}).\n"
        "       PyInstaller does not support Python 3.13+ for frozen Streamlit apps.\n"
        "       Run:  py -3.11 build.py  — or use build.bat which selects it automatically."
    )


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller not found — installing...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller>=6.0"],
            check=True,
        )


def clean() -> None:
    for name in ("build_tmp", "dist"):
        p = ROOT / name
        if p.exists():
            try:
                shutil.rmtree(p)
                print(f"  Removed {p}")
            except PermissionError:
                sys.exit(
                    f"\nERROR: Cannot delete {p} — the exe is still running.\n"
                    "  Close DITA-Converter.exe and run build.bat again."
                )


def build(debug: bool) -> None:
    launcher = ROOT / "build" / "launcher.py"
    if not launcher.exists():
        sys.exit(f"ERROR: {launcher} not found.")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", "DITA-Converter",
        "--onefile",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build_tmp"),
        "--specpath", str(ROOT / "build"),

        # ---- Data files packed into the exe --------------------------------
        "--add-data", f"{ROOT / 'config'};config",
        "--add-data", f"{ROOT / 'ui'};ui",
        "--add-data", f"{ROOT / '.streamlit'};.streamlit",

        # ---- Package metadata (avoids importlib.metadata errors) -----------
        "--copy-metadata", "streamlit",
        "--copy-metadata", "pdfplumber",
        "--copy-metadata", "lxml",
        "--copy-metadata", "pymupdf",
        "--copy-metadata", "python-docx",
        "--copy-metadata", "PyYAML",
        "--copy-metadata", "pillow",

        # ---- Streamlit static assets + sub-packages ------------------------
        "--collect-all", "streamlit",
        # tornado must be fully collected — partial inclusion breaks socket binding
        "--collect-all", "tornado",

        # ---- Hidden imports PyInstaller misses ------------------------------
        "--hidden-import", "streamlit.web.cli",
        "--hidden-import", "streamlit.runtime.scriptrunner.magic_funcs",
        "--hidden-import", "streamlit.components.v1",
        "--hidden-import", "tornado.platform.asyncio",
        "--hidden-import", "tornado.platform.windows",
        "--hidden-import", "agents.extractor",
        "--hidden-import", "agents.mapper",
        "--hidden-import", "agents.generator",
        "--hidden-import", "agents.validator",
        "--hidden-import", "agents.image_processor",
        "--hidden-import", "agents.pdf_quality",
        "--hidden-import", "pdfplumber",
        "--hidden-import", "fitz",          # pymupdf
        "--hidden-import", "lxml.etree",
        "--hidden-import", "yaml",
        "--hidden-import", "docx",
        "--hidden-import", "PIL",
        "--hidden-import", "PIL.Image",

        str(launcher),
    ]

    # Console mode: keep for --debug, suppress for release
    if debug:
        cmd.append("--console")
        print("  Mode: DEBUG (console window visible)")
    else:
        cmd.append("--windowed")
        print("  Mode: RELEASE (no console window)")
        print("  Tip: run with --debug first if the exe fails silently.")

    print()
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        sys.exit(result.returncode)

    exe = ROOT / "dist" / "DITA-Converter.exe"
    size_mb = exe.stat().st_size / 1_048_576 if exe.exists() else 0
    print(f"\nBuild complete  →  {exe}  ({size_mb:.1f} MB)")
    print("Note: first launch unpacks ~200 MB to a temp folder — expect 10-30 s.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build DITA-Converter.exe")
    parser.add_argument("--debug", action="store_true",
                        help="Keep console window open (useful for diagnosing startup errors)")
    parser.add_argument("--no-clean", action="store_true",
                        help="Skip cleaning dist/ and build_tmp/ before building")
    args = parser.parse_args()

    print("=" * 60)
    print("  DITA Converter — EXE Build")
    print("=" * 60)

    ensure_pyinstaller()

    if not args.no_clean:
        print("\nCleaning previous build artifacts...")
        clean()

    print("\nBuilding...")
    build(debug=args.debug)


if __name__ == "__main__":
    main()

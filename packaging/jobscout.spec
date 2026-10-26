# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for the jobscout desktop app (the GUI, not the CLI).

Build it from the repo root:

    uv sync --extra app
    uv pip install "pyinstaller>=6.22,<7"     # build tool, not a runtime dep
    uv run --no-sync pyinstaller packaging/jobscout.spec

Output: ``dist/jobscout/`` (onedir bundle; the executable inside is named
``jobscout``) and, on macOS, ``dist/jobscout.app``. Add ``--noconfirm`` to
overwrite an existing ``dist/`` and ``--clean`` to throw away the cached
analysis in ``build/``.

PyInstaller cannot cross-compile, so this file is written for whatever OS it is
run on: a macOS run produces the .app, Windows and Linux produce the plain
onedir executable, and the BUNDLE step only exists on darwin.

Four things about this repo decide whether the bundle works, all verified
against the source rather than assumed:

* ``jobscout/web/*`` is loaded from disk at runtime
  (``WEB = Path(__file__).resolve().parent / "web"`` in jobscout/app.py).
  Without the data files the window pops up blank -- which reads as a UI bug,
  not as a packaging bug.
* ``jobscout/sources/<name>.py`` is imported *by name*
  (``importlib.import_module(f".{name}", __package__)`` in
  jobscout/sources/__init__.py), which static analysis cannot see. Every
  adapter is listed below and cross-checked against the directory, so adding
  an adapter without touching this file fails the build loudly instead of
  shipping an app that silently loads nothing.
* ``geonamescache`` ships as a package with a JSON gazetteer that it opens via
  ``open(os.path.dirname(__file__) + "/data/...")`` -- so its data files have to
  be collected, or geo.py degrades to tiers 1+3 (no city resolution) at runtime.
* pywebview chooses its backend at runtime by module name
  (``webview/guilib.py`` imports ``webview.platforms.<backend>`` inside a
  function), and ships its own PyInstaller hook at ``webview/__pyinstaller/``
  for ``webview/js`` and the Windows ``webview/lib`` DLLs. The backend module
  itself is added per platform below.

User data is deliberately absent: ``~/.jobscout/config.yaml``,
``~/.jobscout/jobscout.db`` and ``reports/`` live in the user's home directory
and must never be baked into a distributable.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

APP_NAME = "jobscout"
BUNDLE_ID = "com.jpetsis.jobscout"

# SPECPATH is injected by PyInstaller: the directory holding this spec.
# packaging/jobscout.spec -> <repo>/packaging -> <repo>
ROOT = Path(SPECPATH).resolve().parent                     # noqa: F821
PACKAGE = ROOT / "jobscout"
ICONS = Path(SPECPATH).resolve() / "icons"                 # noqa: F821

# The app's icon is the logo mark alone; packaging/make_icons.py builds these.
WANT_ICON = {"darwin": "jobscout.icns", "win32": "jobscout.ico"}.get(sys.platform)
if WANT_ICON and not (ICONS / WANT_ICON).is_file():
    raise SystemExit(
        f"packaging/icons/{WANT_ICON} is missing; run "
        "`uv run --with pillow python packaging/make_icons.py` "
        "(it needs a Chrome: any build of it will do)")

if not (ROOT / "pyproject.toml").is_file():
    raise SystemExit(
        "packaging/jobscout.spec must live in <repo>/packaging: no pyproject.toml "
        f"found in {ROOT}")

try:
    VERSION = str(tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
                  ["project"]["version"])
except (OSError, KeyError, tomllib.TOMLDecodeError):
    VERSION = "0.0.0"

# --- entry point -----------------------------------------------------------
# jobscout/app.py uses relative imports, so it cannot be PyInstaller's entry
# script (that is executed as top-level __main__). This shim imports
# ``jobscout.app:main`` by absolute name instead.
ENTRY = Path(SPECPATH).resolve() / "jobscout_entry.py"      # noqa: F821
if not ENTRY.is_file():
    raise SystemExit(f"missing frozen-app entry script: {ENTRY}")

# --- the dynamically imported source adapters -------------------------------
# Keep in sync with SOURCE_NAMES in jobscout/sources/__init__.py; the check
# below turns a drift into a build error.
SOURCE_MODULES = [
    "linkedin", "stepstone", "xing", "stellenanzeigen", "karriere_at", "jobs_ch",
    "arbeitnow", "berlinstartupjobs", "ams", "eures", "jobsat", "hn", "remotive", "wwr",
    "remoteok", "larajobs", "workingnomads", "nodesk", "himalayas",
    "greenhouse", "ashby", "recruitee", "lever", "smartrecruiters", "workable",
    "themuse", "workday",
    "adzuna", "usajobs",
    "germantechjobs", "swissdevjobs", "jobup", "jobscout24", "jobware", "talent",
    "englishjobs", "governmentjobs", "jobicy", "teamtailor", "join",
    "wordpressjobs", "pythonjobs", "fossjobs",
    "academics", "wissenschaftskarriere", "euraxess", "myscience",
    "instaffo", "ycombinator", "wellfound", "dice",
]
_on_disk = sorted(p.stem for p in (PACKAGE / "sources").glob("*.py") if p.name != "__init__.py")
if sorted(SOURCE_MODULES) != _on_disk:
    raise SystemExit(
        "packaging/jobscout.spec is out of sync with jobscout/sources/:\n"
        f"  listed here but not on disk: {sorted(set(SOURCE_MODULES) - set(_on_disk))}\n"
        f"  on disk but not listed here: {sorted(set(_on_disk) - set(SOURCE_MODULES))}\n"
        "Update SOURCE_MODULES here (and SOURCE_NAMES in jobscout/sources/__init__.py) "
        "and rebuild.")

hiddenimports = [f"jobscout.sources.{name}" for name in SOURCE_MODULES]

# --- pywebview backend, picked at runtime by name ---------------------------
if importlib.util.find_spec("webview") is None:
    raise SystemExit("pywebview is not installed -- run `uv sync --extra app` before building")

if sys.platform == "darwin":
    hiddenimports += ["webview.platforms.cocoa"]
elif sys.platform == "win32":
    hiddenimports += [
        "webview.platforms.winforms",       # WebView2 through pythonnet (default)
        "webview.platforms.webview2core",   # the .NET bridge winforms loads
        "webview.platforms.win32",
    ]
else:
    # pywebview has no default Linux backend; pyproject.toml declares
    # pywebview[qt] there (PyQt6 + Qt WebEngine). GTK is kept in the list so a
    # GTK-Python environment still finds its backend; if it cannot be imported
    # pywebview falls back to Qt by itself.
    hiddenimports += ["webview.platforms.qt", "webview.platforms.gtk"]

# --- data files -------------------------------------------------------------
WEB_DIR = PACKAGE / "web"
_web_files = [p for p in sorted(WEB_DIR.rglob("*")) if p.is_file()]
if not (WEB_DIR / "index.html").is_file():
    raise SystemExit(
        f"jobscout/web/index.html is missing ({WEB_DIR}); the app would open a blank window")

# Every file under web/ keeps its relative place: dest "jobscout/web[/subdir]",
# so Path(jobscout.__file__).parent / "web" resolves inside the bundle.
datas = [
    (str(p), "/".join(("jobscout", "web", *p.relative_to(WEB_DIR).parts[:-1])))
    for p in _web_files
]


def _package_data(name: str) -> list[tuple[str, str]]:
    """Data files of a dependency, with a message that says what to do."""
    try:
        return collect_data_files(name)
    except Exception as exc:                                   # noqa: BLE001
        raise SystemExit(
            f"could not collect data files for '{name}' ({exc}); "
            "run `uv sync --extra app` in the repo root first") from exc


datas += _package_data("geonamescache")    # offline gazetteer for geo.py tier 2

# The CA bundle update.py builds its TLS context from. PyInstaller ships its own
# OpenSSL, which has NO certificate store, so without this file a packaged app
# cannot verify any https host at all -- the update check failed silently on
# every launch, looking exactly like "you are up to date".
_certifi_files = _package_data("certifi")
if not any(Path(src).name == "cacert.pem" for src, _ in _certifi_files):
    raise SystemExit(
        "certifi's cacert.pem is not being bundled; update.py builds its TLS "
        "context from it and the packaged app cannot reach GitHub without it")
datas += _certifi_files

# --- the system libraries Qt WebEngine needs and PyInstaller misses ----------
# Qt's WebEngine links libminizip and libopus. PyInstaller collects Qt as data
# files, so it never walks those dependencies, and a minimal distribution has
# neither: `import qtpy.QtWebEngineWidgets` then dies with "cannot open shared
# object file", which pywebview reports as "install QT or GTK". Copying the two
# in leaves the GL libraries as the only system requirement.
binaries = []


def _find_shared_library(soname: str) -> str | None:
    try:
        listing = subprocess.run(["ldconfig", "-p"], capture_output=True,
                                 text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in listing.splitlines():
        if soname in line and "=>" in line:
            return line.split("=>")[-1].strip()
    return None


if sys.platform.startswith("linux"):
    for _soname in ("libminizip.so.1", "libopus.so.0"):
        _path = _find_shared_library(_soname)
        if _path and Path(_path).is_file():
            binaries.append((_path, "."))
        else:
            print(f"warning: {_soname} not found on this build host; the "
                  f"bundle will rely on the system copy", file=sys.stderr)

# --- build ------------------------------------------------------------------
a = Analysis(                                              # noqa: F821
    [str(ENTRY)],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)                                          # noqa: F821

exe = EXE(                                                 # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # onedir: binaries go into COLLECT, not the exe
    name=APP_NAME,
    icon=str(ICONS / WANT_ICON) if WANT_ICON else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,                    # stripping breaks the macOS ad-hoc signature
    upx=False,                      # UPX corrupts Qt/pyobjc binaries; do not enable
    console=False,                  # windowed: no terminal window
    codesign_identity=None,         # unsigned on purpose; signing is separate work
    entitlements_file=None,
)

coll = COLLECT(                                            # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(                                          # noqa: F821
        coll,
        name=f"{APP_NAME}.app",
        icon=str(ICONS / "jobscout.icns"),
        bundle_identifier=BUNDLE_ID,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
        },
    )

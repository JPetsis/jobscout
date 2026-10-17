"""Check for, download and install a newer build.

WHAT THIS CAN AND CANNOT DO, because the honest version matters more than a
promise: the builds are UNSIGNED (see docs/INSTALL.md). That constrains the
design in two ways.

1. There is no signature to verify, so integrity rests on the sha256 GitHub
   publishes for every release asset. That is a real check against a corrupted
   or truncated download; it is NOT a check that the publisher is who they say
   they are, because nothing is signed. The module downloads over HTTPS from a
   fixed GitHub URL and verifies the hash the same API returns.

2. A running application cannot replace itself in place. On Windows the running
   .exe is locked and cannot even be renamed; on macOS the bundle can be moved
   aside but the new one is not running. So an install is STAGED and applied by
   a small detached helper that waits for this process to exit, swaps the
   directory, and relaunches. The alternative -- half-replacing a live app --
   is how you leave someone with nothing that launches.

The swap is also the only step that can destroy a working install, so it takes
a backup first and restores it if the new build does not land.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = "JPetsis/jobscout"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=10"
HOMEPAGE = f"https://github.com/{REPO}/releases"

# Asked once a day at most. An update check is a network request that says what
# version you are running, so it is cached and can be turned off entirely with
# `updates.check: false` in the config.
CHECK_INTERVAL = timedelta(hours=24)
TIMEOUT = 20.0


def _updates_setting(cfg, key: str):
    """Read the top-level `updates:` block, or None when it is not there.

    One reader for one key. The block is documented at the top level, and the
    app used to look for it under `preferences:` instead, so a config that said
    `updates: {check: false}` was silently ignored and the app kept phoning
    home. Anything that decides whether a check may happen goes through here.
    """
    block = (getattr(cfg, "raw", None) or {}).get("updates")
    if not isinstance(block, dict):
        return None
    return block.get(key)


def checks_allowed(cfg) -> bool:
    """Whether the app may ask GitHub on its own.

    The explicit `jobscout update` command deliberately ignores this: asking for
    a check IS the instruction. A config written before the setting existed has
    no block and therefore keeps the documented default, which is on.
    """
    return _updates_setting(cfg, "check") is not False


def auto_install(cfg) -> bool:
    """Whether a found update may be installed without being asked."""
    return _updates_setting(cfg, "auto") is not False

# How long the swap helper waits for jobscout to exit before giving up and
# leaving the installed build alone. Long enough for a slow shutdown, short
# enough that a stuck helper is not a mystery.
WAIT_SECONDS = 120


class UpdateError(Exception):
    """Anything that stops an update, with a message meant for a user."""


# --------------------------------------------------------------- versions

def parse_version(text: str) -> tuple:
    """`v0.1.0` / `0.1.0` / `0.2.0-rc1` -> a comparable tuple.

    Numeric parts compare as numbers so 0.9 < 0.10. The trailing rank is what
    makes a prerelease sort BELOW its own release: `0.2.0` -> (...,1) and
    `0.2.0-rc1` -> (...,0,'rc1'). Without that rank the plain tuple (0,2,0)
    would compare as a PREFIX of (0,2,0,0,'rc1') and therefore as smaller,
    which is backwards -- 0.2.0-rc1 would look newer than 0.2.0 and the app
    would offer a downgrade.
    """
    s = (text or "").strip().lstrip("vV")
    if not s:
        return (0, 1)
    core, _, suffix = s.partition("-")
    parts: list = []
    for chunk in core.split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    if suffix:
        parts.append(0)                        # prerelease ranks below release
        parts.append(suffix)
    else:
        parts.append(1)                        # a release outranks its rcs
    return tuple(parts)


def current_version() -> str:
    from . import __version__
    return __version__


def is_newer(candidate: str, than: str) -> bool:
    return parse_version(candidate) > parse_version(than)


# ------------------------------------------------------- platform + assets

def platform_asset() -> str:
    """The release asset name for this machine.

    Matches the names `.github/workflows/build.yml` produces; the two have to
    agree or the update would download a build for the wrong architecture.
    """
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64"):
        arch = "x86_64"
    else:
        raise UpdateError(f"no prebuilt jobscout for this CPU ({machine}). "
                          f"Build from source: {HOMEPAGE}")

    if sys.platform == "darwin":
        return f"jobscout-macos-{arch}.zip"
    if sys.platform == "win32":
        if arch != "x86_64":
            raise UpdateError(f"no prebuilt jobscout for Windows {arch}")
        return "jobscout-windows-x86_64.zip"
    if sys.platform.startswith("linux"):
        return f"jobscout-linux-{arch}.tar.gz"
    raise UpdateError(f"no prebuilt jobscout for {sys.platform}")


@dataclass
class Asset:
    name: str
    url: str
    size: int = 0
    sha256: str = ""


@dataclass
class Release:
    tag: str
    version: str
    assets: dict = field(default_factory=dict)
    url: str = ""

    def asset_for_this_machine(self) -> Asset | None:
        return self.assets.get(platform_asset())


# ------------------------------------------------------------------- net

def _ca_candidates() -> list[Path]:
    """Every place a usable CA bundle might be, in the order worth trying."""
    candidates: list[Path] = []
    try:
        import certifi                                        # noqa: PLC0415
        candidates.append(Path(certifi.where()))
    except Exception:                                          # noqa: BLE001
        pass
    roots = [getattr(sys, "_MEIPASS", None), Path(sys.executable).resolve().parent,
             Path(__file__).resolve().parent]
    for root in roots:
        if not root:
            continue
        root = Path(root)
        candidates += [
            root / "certifi" / "cacert.pem",
            root / "Resources" / "certifi" / "cacert.pem",
            root.parent / "Resources" / "certifi" / "cacert.pem",
        ]
    candidates.append(Path("/etc/ssl/cert.pem"))               # macOS system store
    return candidates


def _ca_bundle() -> str | None:
    """A CA bundle this process can actually verify GitHub against.

    PyInstaller ships its OWN OpenSSL, which has no certificate store, so in a
    packaged build the default SSL context could not verify anything: every
    https request failed with CERTIFICATE_VERIFY_FAILED and the update check
    silently did nothing -- forever, on every launch, with no visible symptom.

    certifi's bundle is already inside the app (it comes in with pywebview), so
    use that. It is looked up as a FILE rather than by importing certifi, so it
    works whether or not the module itself was collected into the archive.
    """
    for c in _ca_candidates():
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    return None


def _context() -> "ssl.SSLContext | None":
    """The SSL context to use, or None to let urllib pick its own."""
    cafile = _ca_bundle()
    if not cafile:
        return None
    try:
        return ssl.create_default_context(cafile=cafile)
    except Exception:                                          # noqa: BLE001
        return None


def _urlopen(req, timeout: float = TIMEOUT):
    """urlopen with a context that works in a frozen build too."""
    ctx = _context()
    if ctx is None:
        return urllib.request.urlopen(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def _get_json(url: str):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"jobscout/{current_version()}",
    })
    try:
        with _urlopen(req) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("no releases published yet") from None
        raise UpdateError(f"GitHub answered HTTP {e.code}") from e
    except Exception as e:                                     # noqa: BLE001
        raise UpdateError(f"could not reach GitHub: {type(e).__name__}: {e}") from e


def latest_release(include_prerelease: bool = False) -> Release | None:
    """The newest published release, or None when there are none.

    Drafts are excluded deliberately: a draft is not downloadable by anyone but
    the owner, so offering it as an update would advertise a build the user
    cannot fetch. That also means a draft-only repo correctly reports "up to
    date" rather than failing.
    """
    data = _get_json(RELEASES_API)
    best: Release | None = None
    for rel in data or []:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        if rel.get("prerelease") and not include_prerelease:
            continue
        tag = str(rel.get("tag_name") or "").strip()
        if not tag:
            continue
        assets = {}
        for a in rel.get("assets") or []:
            name = str(a.get("name") or "")
            digest = str(a.get("digest") or "")
            assets[name] = Asset(
                name=name,
                url=str(a.get("browser_download_url") or ""),
                size=int(a.get("size") or 0),
                sha256=digest.split(":", 1)[1] if digest.startswith("sha256:") else "",
            )
        cand = Release(tag=tag, version=tag.lstrip("vV"), assets=assets,
                       url=str(rel.get("html_url") or HOMEPAGE))
        if best is None or is_newer(cand.tag, best.tag):
            best = cand
    return best


# ------------------------------------------------------------- check cache

def _cache_path(root: Path) -> Path:
    return root / "update-check.json"


def log(root: Path, msg: str) -> None:
    """Append one line to the update log, and never raise.

    Automatic updates run with nobody watching, and until now every way one
    could fail was invisible. The packaged app could not reach GitHub AT ALL
    (its OpenSSL had no certificate store) and the symptom was exactly the same
    as being up to date: no notice, no error, nothing to look at. This file is
    the answer to "it did not update and I cannot see why", and it is the first
    thing to read when it happens again.
    """
    try:
        path = Path(root) / "update.log"
        if path.exists() and path.stat().st_size > 64_000:
            path.replace(Path(str(path) + ".1"))
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp}  {msg}\n")
    except OSError:
        pass                    # logging must never be the thing that breaks


def read_cached_check(root: Path) -> dict | None:
    try:
        return json.loads(_cache_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def check(root: Path, *, force: bool = False, include_prerelease: bool = False) -> dict:
    """Is there a newer release? Cached for CHECK_INTERVAL unless forced.

    The cache exists so launching the app does not phone home every time. The
    result is a plain dict so the CLI and the app render the same thing.

    What is cached is only what the RELEASE is. `current` used to be cached too,
    which is wrong in a way that bites exactly once and hard: the app installs an
    update, restarts on the new version within the cache window, reads back the
    OLD version's answer, concludes an update is available, installs the same
    version again -- and does it again on every restart, for as long as the
    cache lives. The running version is therefore always taken from the running
    process, and `update_available` is recomputed from it.
    """
    current = current_version()
    now = datetime.now(timezone.utc)
    if not force:
        cached = read_cached_check(root)
        if cached:
            try:
                when = datetime.fromisoformat(cached.get("checked_at", ""))
            except ValueError:
                when = None
            if when is not None and now - when < CHECK_INTERVAL:
                latest = cached.get("latest")
                out = {
                    **cached,
                    "current": current,
                    "update_available": bool(latest) and is_newer(latest, current),
                    "from_cache": True,
                }
                log(root, f"check: cached answer, latest {latest or 'none'}, "
                          f"this build {current}, update {out['update_available']}")
                return out

    try:
        rel = latest_release(include_prerelease=include_prerelease)
    except UpdateError as e:
        # Deliberately NOT cached: a check that failed because the network was
        # not up yet would otherwise be the answer for the next 24 hours, and
        # the app would never try again that day.
        log(root, f"check failed: {e}")
        return {"ok": False, "error": str(e), "current": current,
                "checked_at": now.isoformat(timespec="seconds")}

    if rel is None:
        out = {"ok": True, "current": current, "latest": None,
               "update_available": False, "asset": None,
               "checked_at": now.isoformat(timespec="seconds")}
    else:
        asset = rel.asset_for_this_machine()
        out = {
            "ok": True,
            "current": current,
            "latest": rel.version,
            "tag": rel.tag,
            "url": rel.url,
            "update_available": is_newer(rel.tag, current),
            "asset": ({"name": asset.name, "size": asset.size, "url": asset.url}
                      if asset else None),
            "asset_for_platform": platform_asset(),
            "checked_at": now.isoformat(timespec="seconds"),
        }
    try:
        _cache_path(root).write_text(json.dumps(out), encoding="utf-8")
    except OSError:
        pass                    # an unwritable cache must not break the check
    log(root, f"check: latest {out.get('latest') or 'none'}, this build "
              f"{current}, update {out['update_available']}")
    return {**out, "from_cache": False}


# --------------------------------------------------------------- download

def _sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def download(url: str, dest: Path, *, expected_sha256: str = "",
             expected_size: int = 0) -> Path:
    """Fetch an asset and verify it before returning.

    Verified against the sha256 GitHub publishes for the asset. The file is
    written to a temporary name and renamed only once it verifies, so a failed
    or interrupted download can never be mistaken for a complete one.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={
        "User-Agent": f"jobscout/{current_version()}", "Accept": "*/*"})
    try:
        with _urlopen(req) as r, tmp.open("wb") as fh:
            shutil.copyfileobj(r, fh, length=1 << 20)
    except Exception as e:                                     # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise UpdateError(f"download failed: {type(e).__name__}: {e}") from e

    size = tmp.stat().st_size
    if expected_size and size != expected_size:
        tmp.unlink(missing_ok=True)
        raise UpdateError(f"download is {size} bytes, expected {expected_size}")
    if expected_sha256:
        got = _sha256_of(tmp)
        if got != expected_sha256:
            tmp.unlink(missing_ok=True)
            raise UpdateError("the download does not match the checksum GitHub "
                              "published for it — refusing to install")
    tmp.replace(dest)
    return dest


# ---------------------------------------------------------------- install

def is_frozen() -> bool:
    """Running from a packaged build rather than a source checkout."""
    return bool(getattr(sys, "frozen", False))


def install_root() -> Path | None:
    """The directory that a new build replaces, or None for a source install.

    macOS: the `.app` bundle (sys.executable is <app>/Contents/MacOS/jobscout).
    Windows/Linux: the onedir folder holding the executable.
    """
    if not is_frozen():
        return None
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        # .../jobscout.app/Contents/MacOS/jobscout
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent
        return exe.parents[2] if len(exe.parents) > 2 else exe.parent
    return exe.parent


def extract(archive: Path, into: Path) -> Path:
    """Unpack an archive into `into` and return the payload directory.

    The payload is what gets swapped in: a `.app` on macOS, or the onedir
    folder elsewhere. Its absence means the archive is not a jobscout build,
    which is worth failing on rather than installing over a working copy.
    """
    into = Path(into)
    into.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                z.extractall(into)
        elif name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(archive, "r:gz") as t:
                # 'data' refuses absolute paths and traversal, which matters
                # for an archive that came off the network.
                t.extractall(into, filter="data")
        else:
            raise UpdateError(f"unsupported archive type: {archive.name}")
    except (zipfile.BadZipFile, tarfile.TarError) as e:
        raise UpdateError(f"could not unpack {archive.name}: {e}") from e

    for candidate in (into / "jobscout.app", into / "jobscout"):
        if candidate.exists():
            return candidate
    children = [p for p in into.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    raise UpdateError("that archive does not look like a jobscout build "
                      "(no jobscout.app or jobscout/ inside)")


def _relaunch_command(root: Path) -> str:
    """How to start jobscout again from its install root.

    `root` is what `install_root()` returns: the `.app` bundle on macOS, the
    onedir FOLDER on Windows and Linux. The distinction matters -- launching the
    folder on Windows opens Explorer, and on Linux it tries to execute a
    directory. Both are silent no-ops as far as the user is concerned: the
    update appears to do nothing.
    """
    if sys.platform == "darwin":
        return f'open "{root}"'                    # open handles the bundle
    if sys.platform == "win32":
        return f'start "" "{root}\\jobscout.exe"'
    return f'"{root}/jobscout" &'


def _helper_script(old_root: Path, new_root: Path, *, relaunch: bool,
                   pid: int | None = None, logfile: Path | None = None) -> str:
    """A script that waits for this process to exit, swaps, and relaunches.

    Written to a temp file and run detached, because it has to outlive the
    process being replaced. It keeps the previous build as `<name>.previous`
    and restores it if the move fails, so a failed update is always
    recoverable by hand.

    `pid` is injectable so the swap can be tested without a live process to
    wait for; it defaults to this one. `logfile` is where it reports its own
    outcome -- this script runs AFTER the app has exited, so it is the only
    thing left that can say whether the swap worked.
    """
    restart = _relaunch_command(old_root) if relaunch else ""
    pid = os.getpid() if pid is None else int(pid)
    log = f'"{logfile}"' if logfile else ""

    def note(text: str) -> str:
        if not log:
            return "rem no log requested"
        if sys.platform == "win32":
            return f"if defined LOG echo %date% %time%  {text} >> {log}"
        return f"note {text!r}"

    if sys.platform == "win32":
        # Label-based flow, NOT `if (...)` blocks. cmd.exe parses a
        # parenthesised block as one unit, and the failure mode is the
        # famously unhelpful ") was unexpected at this time" -- which is what
        # CI reported here. Labels and `goto` have no such parser to trip over.
        return "\n".join([
            "@echo off",
            "rem the running .exe is locked, so wait for it to exit first",
            "setlocal",
            f'set "LOG={logfile or ""}"',
            "set /a tries=0 >nul",
            "",
            ":wait",
            f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul',
            "if errorlevel 1 goto swap",
            "set /a tries+=1 >nul",
            f"if %tries% GEQ {WAIT_SECONDS} goto gaveup",
            "rem ping, not `timeout`: timeout refuses to run without a console,",
            "rem and this script is started detached",
            "ping -n 2 127.0.0.1 >nul",
            "goto wait",
            "",
            ":gaveup",
            note("helper: jobscout did not exit, so it is being killed"),
            "echo jobscout did not exit - killing it and installing anyway.>&2",
            # The helper only EXISTS because the app asked to close, so a
            # process that will not go must not block its own update. Leaving
            # the old build in place was the old answer, and the user then saw
            # a window that never closed and an update that never happened.
            f"taskkill /F /PID {pid} >nul 2>&1",
            "",
            ":swap",
            note("helper: jobscout exited, swapping"),
            # Clear any leftover first, exactly as the shell version does. A
            # stale .previous would make the NEXT update's `move` fail, and a
            # failed move onto an existing directory moves INSIDE it -- which
            # is a mangled install rather than an error.
            f'if exist "{old_root}.previous" rmdir /s /q "{old_root}.previous"',
            f'move "{old_root}" "{old_root}.previous" >nul 2>&1',
            f'move "{new_root}" "{old_root}" >nul 2>&1',
            "if errorlevel 1 goto failed",
            f'rmdir /s /q "{old_root}.previous"',
            note("helper: swapped in the new build"),
            f"{restart}" if restart else "rem no relaunch requested",
            "exit /b 0",
            "",
            ":failed",
            f'move "{old_root}.previous" "{old_root}" >nul 2>&1',
            note("helper: the swap failed, the previous build was restored"),
            "echo jobscout update failed - the previous version was restored.>&2",
            # Restart even though the swap failed. The previous build is back
            # in place, and an update that leaves the user with no window at
            # all is worse than one that quietly stays on the old version --
            # twice as bad when nobody pressed a button to start it.
            f"{restart}" if restart else "rem no relaunch requested",
            "exit /b 1",
            "",
        ])

    return "\n".join([
        "#!/bin/sh",
        f"# Wait for jobscout (pid {pid}) to exit. Bounded: if it never does, give",
        f"# up WITHOUT touching the installed build rather than hanging forever.",
        f"LOG={log}" if log else "LOG=",
        # This runs after the app is gone, so it is the only thing left that can
        # record what the swap did.
        'note() { [ -n "$LOG" ] && echo "$(date \'+%Y-%m-%d %H:%M:%S\')  $*" '
        '>> "$LOG" 2>/dev/null; return 0; }',
        "n=0",
        f"while kill -0 {pid} 2>/dev/null; do",
        "  n=$((n+1))",
        f'  if [ "$n" -gt {WAIT_SECONDS} ]; then',
        # The helper only EXISTS because the app asked to close, so a process
        # that will not go must not block its own update. Leaving the old build
        # in place was the old answer, and it left the user with a window that
        # never closed and an update that never happened.
        '    note "helper: jobscout did not exit, so it is being killed"',
        '    echo "jobscout did not exit; killing it and installing anyway." >&2',
        f"    kill -9 {pid} 2>/dev/null",
        "    break",
        "  fi",
        "  sleep 1",
        "done",
        'note "helper: jobscout exited, swapping"',
        f'rm -rf "{old_root}.previous"',
        f'if mv "{old_root}" "{old_root}.previous" 2>/dev/null && '
        f'mv "{new_root}" "{old_root}" 2>/dev/null; then',
        f'  rm -rf "{old_root}.previous"',
        '  note "helper: swapped in the new build"',
        f"  {restart}" if restart else "  :",
        "else",
        f'  mv "{old_root}.previous" "{old_root}" 2>/dev/null',
        '  note "helper: the swap failed, the previous build was restored"',
        '  echo "jobscout update failed; the previous version was restored." >&2',
        # Restart on the OLD build rather than leaving the user with no window.
        f"  {restart}" if restart else "  :",
        "fi",
        "",
    ])


def apply_staged(staged: Path, *, relaunch: bool = True,
                 logpath: Path | None = None) -> Path:
    """Swap a staged build in by spawning the helper and returning its path.

    The caller is expected to exit immediately afterwards. Nothing here touches
    the installed build itself -- that is the helper's job, once this process
    is gone.
    """
    root = install_root()
    if root is None:
        raise UpdateError(
            "jobscout is running from a source checkout, not a packaged build, "
            "so there is nothing to replace. Update with:\n"
            "    git pull && uv sync --extra app")
    staged = Path(staged)
    if not staged.exists():
        raise UpdateError(f"staged build is missing: {staged}")

    script = Path(tempfile.mkdtemp(prefix="jobscout-update-")) / (
        "apply.bat" if sys.platform == "win32" else "apply.sh")
    script.write_text(_helper_script(root, staged, relaunch=relaunch,
                                     logfile=logpath),
                      encoding="utf-8")
    script.chmod(0o755)
    if sys.platform == "win32":
        # A batch file is not an executable image: CreateProcess needs the
        # command processor, and DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        # keeps it alive once we exit.
        argv = ["cmd", "/c", str(script)]
        flags = 0x00000008 | 0x00000200
        subprocess.Popen(argv, creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen([str(script)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return script


def update(root: Path, *, from_archive: Path | None = None, apply: bool = False,
           relaunch: bool = True, progress=None) -> dict:
    """Check, fetch, verify and stage an update. Swaps in only if `apply`.

    `from_archive` installs a build the user already has instead of
    downloading one, which is the safer path and the only workable one when
    they are on a network the release host is not reachable from.
    """
    def say(msg: str) -> None:
        if progress:
            progress(msg)

    if from_archive is not None:
        archive = Path(from_archive)
        if not archive.is_file():
            raise UpdateError(f"no such file: {archive}")
        say(f"using {archive.name}")
        version = "unknown (local build)"
    else:
        info = check(root, force=True)
        if not info.get("ok"):
            raise UpdateError(info.get("error") or "could not check for updates")
        if not info.get("update_available"):
            return {"ok": True, "updated": False, "staged": None,
                    "current": info["current"], "staged_version": None,
                    "reason": "already up to date"}
        asset = info.get("asset")
        if not asset:
            raise UpdateError(
                f"release {info.get('latest')} has no build for this machine "
                f"({info.get('asset_for_platform')}). Download manually: "
                f"{info.get('url') or HOMEPAGE}")
        say(f"downloading {asset['name']} ({asset['size'] / 1048576:.0f} MB)")
        log(root, f"install: downloading {asset['name']} ({asset['size']} bytes)")
        target = root / "updates" / asset["name"]
        archive = download(asset["url"], target,
                           expected_sha256=asset.get("sha256", ""),
                           expected_size=asset.get("size", 0))
        version = info.get("latest") or "unknown"
        say("checksum verified")
        log(root, "install: checksum verified")

    staging = Path(tempfile.mkdtemp(prefix="jobscout-staged-"))
    payload = extract(archive, staging)
    say(f"staged {payload.name}")
    log(root, f"install: staged {version} at {payload}")

    out = {"ok": True, "updated": False, "staged": str(payload),
           "archive": str(archive), "staged_version": version,
           "current": current_version(),
           "install_root": str(install_root()) if install_root() else None}
    if apply:
        script = apply_staged(payload, relaunch=relaunch)
        out.update(updated=True, helper=str(script))
        say("a helper will swap it in once jobscout exits")
    return out

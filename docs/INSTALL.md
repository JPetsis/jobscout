# Installing jobscout

jobscout ships as an unsigned desktop app. Nothing here costs money, but every
platform shows some version of "this app is from an unidentified developer" the
first time, and two of the three need a specific sequence to get past it.

Read the section for your system. It is a one-time step per downloaded copy.

---

## Windows

**You need:** Windows 10 1803 or newer (Windows 11 is fine). Nothing to install:
the Microsoft Edge WebView2 runtime that draws the window is already part of
those versions, and Qt is not used on Windows at all.

Windows Server and LTSC images ship without Edge and therefore without that
runtime. The download carries Microsoft's installer for exactly this case: start
jobscout, answer **Yes** to its one question, and it installs the runtime itself.
If that copy is somehow missing, the app names the download page instead.

**First run:**

1. Download `jobscout-windows-x86_64.zip` from the releases page and unzip it
   anywhere — your Desktop is fine. Keep the whole folder together; the
   `.exe` needs the files next to it.
2. Double-click `jobscout.exe`.
3. Windows shows a blue box: **"Windows protected your PC"**. Click
   **More info**, then **Run anyway**.

That is the whole dance. Windows remembers nothing, so **you will see the same
warning again on the next version you download** — this is expected, and it is
what signing would fix.

If there is no **Run anyway** button at all, your PC has **Smart App Control**
on (it is enabled only on clean Windows 11 installs). Turn it off under
**Windows Security → App & browser control → Smart App Control**. As of the
April 2026 Windows update this can be toggled without reinstalling Windows.

---

## macOS

**You need:** macOS 13 or newer. A PyInstaller bundle built for your
architecture — `macos-arm64` for Apple Silicon (M1 and later), `macos-x86_64`
for Intel. If you pick the wrong one it will not launch at all.

**First run:**

1. Download the right `.zip` for your Mac and unzip it.
2. Move `jobscout.app` to your Applications folder.
3. Try to open it. macOS blocks it: *"jobscout cannot be opened because the
   developer cannot be verified."* Click **Done**. Do **not** click
   *Move to Trash*.
4. Open **System Settings → Privacy & Security** and scroll to the
   **Security** section. You will see *"jobscout was blocked from use…"* with
   an **Open Anyway** button.
5. Click **Open Anyway**, confirm, and enter your password. macOS now keeps a
   permanent exception for this copy.

Three things that surprise people:

- The **Open Anyway button is only there for about an hour** after the blocked
  attempt, and nothing on screen counts down. If it is gone, try to open the
  app again to re-trigger the block and the button comes back.
- On macOS 15.1.1 and later, **only the most recently blocked app** gets a
  button. If you tried to open two unsigned apps, only one of them will have
  one — open the other again to make it the most recent.
- The old right-click → Open trick **no longer works** — Apple removed it in
  macOS 15 (Sequoia).

**If it says the app "is damaged and can't be opened. You should move it to the
Trash"** — that is a different failure, and Open Anyway will not fix it. It
happens when something in the download path strips extended attributes. The fix
is one Terminal command:

```bash
xattr -cr /Applications/jobscout.app
```

Then open the app normally. If you are not comfortable with Terminal, re-download
the zip with a real browser (some download managers strip the attributes) and
unzip it with Finder's own "Open" rather than a third-party archiver.

> This step is the reason signing is on the roadmap. There is no free Apple
> programme that fixes it, so the only alternatives are $99/year for a Developer
> account or living with the prompt. macOS users currently live with it.

---

## Linux

**You need:** a desktop session. The packaged build carries Qt and its WebEngine
inside the tarball, so there is no Qt to install. Every normal desktop already
has what is left, but a minimal system (a container, a bare window manager) may
not. If jobscout refuses to start it names the missing library and its package;
on Debian/Ubuntu the ones seen in practice are:

```bash
sudo apt install libegl1 libgl1 fontconfig \
                 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
                 libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xkb1 \
                 libxkbcommon-x11-0 libsm6 libice6
```

Everything above is part of any desktop install; the list is what a bare
container or a minimal window manager was missing when tested. The two libraries
Qt WebEngine links, `libminizip` and `libopus`, are inside the tarball from 0.6.1
on and need no package: on 0.6.0 add `libminizip1t64 libopus0` (Ubuntu 22.04
calls it `libminizip1`).

The two builds are made on different Ubuntu versions, so their floors differ:
the x86_64 tarball runs on Ubuntu 22.04 and newer, the arm64 one is built on
Ubuntu 24.04 and needs glibc 2.38 (Ubuntu 24.04+, Debian 13+).

1. Download `jobscout-linux-x86_64.tar.gz` (or `aarch64` for ARM) and unpack it:

   ```bash
   tar xzf jobscout-linux-x86_64.tar.gz
   ```

2. Run it from the folder you unpacked into:

   ```bash
   ./jobscout/jobscout
   ```

No signing step and no warning — Linux does not gate unsigned binaries.

If it refuses to start, read the message: since 0.5.x it names the exact
library that is missing and the package that provides it, rather than telling
you to install Qt (which the build already contains).

### Ubuntu on WSL2 (Windows)

WSL2 can show Linux windows through WSLg, which ships with WSL on Windows 11 and
with the Store version of WSL on Windows 10 21H2+. Check it with `echo $DISPLAY`
(it should not be empty), then install the same libraries as a bare system:

```bash
sudo apt install libegl1 libgl1 libxcb-cursor0 fontconfig libminizip1t64 libopus0
```

Keep jobscout in the Linux home (`~/jobscout`), not under `/mnt/c`: files there
are slow to read and arrive without the executable bit, so the app either crawls
or refuses to run.

Do not start it as root (`wsl -u root` or a root shell): Chromium's sandbox
refuses to run as root and the window never appears. The normal Ubuntu user WSL
creates for you is fine. If you genuinely need root, prefix the command with
`QTWEBENGINE_DISABLE_SANDBOX=1`, which trades that sandbox away.

---

## First run, on any platform

jobscout keeps everything in `~/.jobscout/` (on Windows:
`C:\Users\<you>\.jobscout\`). You do not have to create that directory: on its
first launch the app writes a commented default config into it and then asks
who is using jobscout, because every profile is a workspace of its own. Name it
or keep **Default** and you are in. A downloaded copy needs nothing else, and no
terminal is involved.

From a terminal, which is the same step taken earlier:

```bash
jobscout init
```

Then:

1. Open the app and click **Settings**. Fill in the job titles you actually
   want, the skills you care about (weighted 1–3), and where you will work.
   Settings writes to `config.yaml` and keeps all of the comments in it, so you
   can also read that file to see what each option does.
2. Click **Scan**. It fetches from the sources enabled in your config and shows
   progress. Press **Cancel** if you want to stop early; sources already
   finished are kept.
3. Cards appear as matches. Click one to read it, then **Save**, **Applied** or
   **Dismiss** — or use `S`, `A`, `X` from the keyboard.

You do not need the CLI for any of that, but `jobscout --help` has it if you
want digests, goals or the terminal triage loop.

---

## Updating

If you installed a packaged build, jobscout updates itself. It checks once a
day, and when a newer release exists it downloads it, verifies the checksum, and
restarts on the new version. The bar across the top says what it is doing. It
does this at startup, before you have started anything, so it never interrupts
work.

By default you are asked, and there are two switches under **Updates** in the
app's settings:

- **Check for updates** — on. Turn it off and jobscout stops asking GitHub at
  all.
- **Install updates automatically** — **off**. Leave it off and a newer version
  is offered instead: press **Install and restart** to download and verify it,
  then **Quit and update** to let it close jobscout and take its place. jobscout
  reopens by itself — you are not closing it by hand. Turn it on if you would
  rather it installed and restarted without asking.

The same two settings are `updates.check` and `updates.auto` in `config.yaml`.
From a terminal, which works either way:

```bash
jobscout update --check     # just tell me
jobscout update --yes       # actually do it
```

Two things worth knowing:

- **No update prompt.** The first install has to get past Gatekeeper or
  SmartScreen because your browser tags what it downloads. jobscout downloads
  updates itself and does not tag them, so an update starts normally. If you
  fetch a release by hand instead, you get the prompt again.
- **You can update from a file.** `jobscout update --from ~/Downloads/jobscout-*.zip`
  installs a build you already have, with the same verification. Useful if you
  are behind a proxy, or off a network that can reach GitHub.

Updates are verified against the checksum GitHub publishes for the download,
and the previous build is restored automatically if the swap fails.

**If it seems not to update**, `~/.jobscout/update.log` says what it did and
when: every check, its result, the download, and what the swap helper did after
jobscout closed. It answers "why didn't it update" without guessing, and it is
the first thing to look at.

---

## Removing the app, and keeping your data

**Your data is not inside the app.** Everything jobscout knows lives in
`~/.jobscout/` (on Windows, `C:\Users\<you>\.jobscout\`), completely separate
from the application. So removing the application cannot touch it.

**On macOS**, drag `jobscout.app` from Applications to the Trash. That is it.
Or, if you prefer a command:

```bash
rm -rf /Applications/jobscout.app
```

Your profile, your database and your reports are untouched and still there next
time you install. Same on Windows and Linux: delete the `jobscout` folder you
unpacked, and `~/.jobscout/` stays where it is.

### Do NOT use a "deep uninstall" tool

This is the one thing that will lose your data. Tools that deep-uninstall or
clean up leftover files work by application name: they see `~/.jobscout/`,
decide it is leftover cruft from the app you just removed, and delete it. It is
not cruft — it holds your profile and every posting you saved, applied to or
dismissed.

AppCleaner and similar utilities on macOS will do this. If you use one, exclude
`~/.jobscout`. Otherwise just delete the app by hand, which is what those tools
do anyway for a single self-contained bundle.

### Back up first anyway

```bash
jobscout backup
```

That writes one archive to `~/jobscout-backups/`, deliberately OUTSIDE
`~/.jobscout/` so a cleanup tool cannot take the backup along with the data.
`jobscout backup --list` shows what you have. If you only have the packaged app
and no `jobscout` command, a plain copy does the same job:

```bash
cp -r ~/.jobscout ~/jobscout-backup
```

If something does remove your workspace, open jobscout again: it will find the
backup and offer to restore it. Or, from a terminal, `jobscout restore`.

### If you use profiles

Everything above still applies, and the data to protect is a little larger.
Each profile is its own workspace under `~/.jobscout/profiles/<name>/`, with its
own `config.yaml` and `jobscout.db`, so the folder to keep is the whole of
`~/.jobscout/` — the base workspace and the `profiles/` directory inside it.
Deep-uninstall tools will happily take both.

Backups are unaffected: `jobscout backup` archives the profile you are currently
using, and removing a profile with `jobscout profile remove NAME --yes` writes
its own backup named after that profile before deleting anything.

### What is worth keeping, and what is not

- `config.yaml` — worth keeping. It is heavily commented and hand-editable, and
  a fresh one is generic. Your edits are the valuable part.
- `jobscout.db` — your triage state. The postings can be rebuilt by scanning
  again, but your saved / applied / dismissed marks cannot be recreated. With
  profiles there is one of these per profile.
- `reports/` — regenerable any time with `jobscout digest --all`.

### Removing everything

Delete the application **and** `~/.jobscout/`, deliberately and in that order.
`~/jobscout-backups/` is separate too, so delete that as well if you want the
backups gone.

---

## Running from source instead

If you have Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/JPetsis/jobscout
cd jobscout
uv sync --extra app
uv run jobscout init
uv run jobscout-app
```

`--extra app` is what pulls in pywebview. On Linux it also installs the Qt
backend, which pywebview does **not** install by default — without it the
window silently fails to appear.

---

## For maintainers: building and releasing

PyInstaller cannot cross-compile, so each platform's artifact is built on that
platform by `.github/workflows/build.yml` (`workflow_dispatch`, or a push of a
`v*` tag). Signing is deliberately **not** wired up: macOS would need a paid
Apple Developer account and Windows a paid certificate.

The one free route for Windows is the [SignPath
Foundation](https://signpath.org/), which signs open-source projects at no
cost. It requires a public repo, an existing CI build and some release history,
so the sequence is: cut an unsigned release, then apply. The release workflow
has a signing step already gated behind the SignPath secrets — it is a no-op
until those exist, so unsigned builds keep working in the meantime.

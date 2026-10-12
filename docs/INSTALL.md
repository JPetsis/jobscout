# Installing jobscout

jobscout ships as an unsigned desktop app. Nothing here costs money, but every
platform shows some version of "this app is from an unidentified developer" the
first time, and two of the three need a specific sequence to get past it.

Read the section for your system. It is a one-time step per downloaded copy.

---

## Windows

**You need:** Windows 10 1803 or newer (Windows 11 is fine). The Microsoft Edge
WebView2 runtime, which jobscout uses to draw its window, is already present on
all of these. It is *not* present on Windows Server or LTSC images; if jobscout
tells you it is missing, install the Evergreen Bootstrapper from
<https://developer.microsoft.com/microsoft-edge/webview2/> and start it again.

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

**You need:** a desktop session with either Qt or GTK available. The packaged
build bundles Qt, so a normal desktop install should just work.

1. Download `jobscout-linux-x86_64.tar.gz` (or `aarch64` for ARM) and unpack it:

   ```bash
   tar xzf jobscout-linux-x86_64.tar.gz
   ```

2. Run it from the folder you unpacked into:

   ```bash
   ./jobscout/jobscout
   ```

No signing step and no warning — Linux does not gate unsigned binaries.

If it exits complaining about a GUI backend, this build was made without Qt.
Install it and use the source install below:

```bash
sudo apt install python3-qtpy
```

---

## First run, on any platform

jobscout keeps everything in `~/.jobscout/` (on Windows:
`C:\Users\<you>\.jobscout\`). On first launch the app will tell you the config
file it wanted and did not find. Create it, then open the app again:

```bash
jobscout init
```

If you installed the packaged app and have no `jobscout` command, run the
initialiser from a source checkout:

```bash
git clone https://github.com/JPetsis/jobscout
cd jobscout
uv sync
uv run jobscout init
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

## Uninstalling, and why you should back up first

**A deep uninstaller will delete your jobscout data.** Tools that "deep
uninstall" or "clean leftover files" match by application name, see
`~/.jobscout/`, and treat it as leftover cruft from the app you just removed.
It is not: that directory holds your profile and every posting you have saved,
applied to or dismissed.

If you use one of these tools, back up first:

```bash
cp -r ~/.jobscout ~/jobscout-backup
```

Then uninstall, and restore if the backup was needed. On macOS the same applies
to AppCleaner and similar utilities; the directory to exclude is `~/.jobscout`
(on Windows, `C:\Users\<you>\.jobscout\`).

Two things worth knowing about what you would lose:

- `config.yaml` is worth backing up on its own. It is heavily commented and
  hand-editable, and a fresh one is generic. Your edits are the valuable part.
- `jobscout.db` holds all triage state. It can be rebuilt by scanning again,
  but your saved/applied/dismissed marks cannot be recreated.

To remove jobscout and keep your data, delete the application only. To remove
everything, delete `~/.jobscout/` as well, deliberately.

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

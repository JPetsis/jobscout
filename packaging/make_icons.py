"""Build the installed app's icon from the logo mark.

The app icon is the badge from ``jobscout/web/logo.svg`` alone: no wordmark.
The name belongs wherever it can be real text (the window's own header), and an
icon has to survive 16 px in a Dock or a taskbar, where a second word would only
be noise.

The layout is the one Apple's icon grid asks for: on a 1024 canvas the badge
keeps a 9.77% margin all round (100 px), so it sits in the Dock at the same
optical size as everything else there.

Rasters are rendered by a headless Chrome rather than by a rasteriser library,
because Chrome is the engine the app itself draws with and it needs nothing
installed:

    uv run --with pillow python packaging/make_icons.py

One trap, learned the hard way: do NOT screenshot the .svg as a document. At
small --window-size values Chrome lays a standalone SVG out at its default
300x150 box and the screenshot is then a crop of it, which produced icons whose
badge hung off the right edge. Render through a one-line HTML wrapper that pins
the <img> to the exact pixel size instead, and keep the check() below, which
fails loudly on exactly that kind of mistake.

Then, per platform:

  * macOS   ``icons/jobscout.icns``  (iconutil; must be built on macOS)
  * Windows ``icons/jobscout.ico``   (Pillow, hence --with pillow)
  * Linux   ``web/icon.png``         (handed to ``webview.start(icon=...)``)

The Linux PNG lives under ``jobscout/web/`` and not here, because the build
collects everything in that directory, so the running app can find it.

The ``.icns`` and ``.ico`` are checked in, so a release build never rasterises:
the files in this directory are the ones that ship. Re-run this only when the
mark itself changes, and look at the result before committing it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast

# Pillow types getpixel() loosely; these images are always RGBA.
Rgba = tuple[int, int, int, int]

HERE = Path(__file__).resolve().parent
ICONS = HERE / "icons"
MARK = HERE.parent / "jobscout" / "web" / "logo.svg"

CANVAS = 1024
SPAN = 60.0                     # the badge's width in the mark's own units
MARGIN = 100 / CANVAS           # Apple's grid: 100 px of air on a 1024 canvas

# iconutil's canonical slots: (points, scale) -> icon_<p>x<p>[@2x].png
ICNS_SLOTS = ((16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2),
              (256, 1), (256, 2), (512, 1), (512, 2))
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

CHROME_CANDIDATES = (
    os.environ.get("CHROME", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    hits = sorted(Path.home().glob(
        "Library/Caches/ms-playwright/*/chrome-mac*/Google Chrome for Testing.app"
        "/Contents/MacOS/Google Chrome for Testing"))
    if hits:
        return str(hits[-1])
    raise SystemExit(
        "no Chrome found; set CHROME=/path/to/chrome (any build of it will do)")


def compose() -> str:
    """Wrap the mark in an icon canvas: same shapes, more air around them."""
    src = MARK.read_text(encoding="utf-8")
    defs = src.split("<defs>", 1)[1].split("</defs>", 1)[0]
    shapes = src.split("</defs>", 1)[1].split("</svg>", 1)[0]
    shapes = shapes.replace("</g>", "").replace("<!--", "").strip()

    scale = CANVAS * (1 - 2 * MARGIN) / SPAN
    shift = CANVAS * MARGIN - 2 * scale         # the badge starts at x=2
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS} {CANVAS}">
  <title>jobscout</title>
  <!-- Generated from jobscout/web/logo.svg by packaging/make_icons.py. -->
  <defs>{defs}</defs>
  <g transform="translate({shift:.4f} {shift:.4f}) scale({scale:.6f})">
{shapes}
  </g>
</svg>
"""


def render(chrome: str, svg: Path, size: int, out: Path) -> None:
    """Screenshot the mark at exactly size x size, transparent background."""
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.html"
        page.write_text(
            "<!doctype html><meta charset='utf-8'><style>"
            "html,body{margin:0;padding:0;background:transparent}"
            f"img{{display:block;width:{size}px;height:{size}px}}"
            f"</style><img src=\"{svg.resolve().as_uri()}\">", encoding="utf-8")
        subprocess.run([
            chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--force-device-scale-factor=1", f"--window-size={size},{size}",
            "--default-background-color=00000000",
            f"--screenshot={out}", page.as_uri(),
        ], check=True, capture_output=True)


def measure(png: Path) -> tuple[str, int, int]:
    """Read back what was actually drawn: margins and the centre pixel."""
    from PIL import Image                                   # noqa: PLC0415
    im = Image.open(png).convert("RGBA")
    w, h = im.size
    mid = h // 2
    opaque = [x for x in range(w) if cast(Rgba, im.getpixel((x, mid)))[3] > 0]
    if not opaque:
        return "empty", w, w
    return "", opaque[0], w - 1 - opaque[-1]


def check(png: Path, label: str, size: int) -> None:
    from PIL import Image                                   # noqa: PLC0415
    problem, left, right = measure(png)
    im = Image.open(png).convert("RGBA")
    w, h = im.size
    centre = cast(Rgba, im.getpixel((w // 2, h // 2)))
    corners = [cast(Rgba, im.getpixel(p))[3]
               for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    want = size * MARGIN
    trouble = []
    if problem:
        trouble.append("nothing was drawn")
    elif abs(left - want) > max(1.5, size * 0.02) or abs(right - want) > max(1.5, size * 0.02):
        trouble.append(f"margin {left}/{right}px, wanted about {want:.1f}")
    if any(corners):
        trouble.append(f"corners are not transparent ({corners})")
    if centre[3] == 0 or not (centre[0] > 200 and centre[1] > 150 and centre[2] < 190):
        trouble.append(f"centre pixel {centre} is not the gold dot")
    state = "ok" if not trouble else "FAILED: " + "; ".join(trouble)
    print(f"    {label:>4}: {w}x{h} margin {left}/{right}px  {state}")
    if trouble:
        raise SystemExit(f"{label}: {state}")


def build_icns(chrome: str, svg: Path, work: Path) -> Path:
    iconset = work / "jobscout.iconset"
    iconset.mkdir(exist_ok=True)
    done: dict[int, Path] = {}
    for points, scale in ICNS_SLOTS:
        px = points * scale
        if px not in done:
            out = iconset / f"icon_{px}x{px}.png"
            render(chrome, svg, px, out)
            check(out, f"{px}px", px)
            done[px] = out
        name = f"icon_{points}x{points}{'@2x' if scale == 2 else ''}.png"
        if name not in (p.name for p in iconset.glob("icon_*")):
            (iconset / name).write_bytes(done[px].read_bytes())
    out = ICONS / "jobscout.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)],
                   check=True)
    return out


def build_ico(chrome: str, svg: Path) -> Path:
    from PIL import Image                                   # noqa: PLC0415
    with tempfile.TemporaryDirectory() as tmp:
        frames = {}
        for size in ICO_SIZES:
            png = Path(tmp) / f"{size}.png"
            render(chrome, svg, size, png)
            check(png, f"{size}px", size)
            frames[size] = Image.open(png).convert("RGBA")
        out = ICONS / "jobscout.ico"
        frames[max(ICO_SIZES)].save(out, format="ICO",
                                    sizes=[(s, s) for s in ICO_SIZES])
    return out


def main() -> int:
    if not MARK.exists():
        raise SystemExit(f"missing mark: {MARK}")
    try:
        import PIL                                          # noqa: F401,PLC0415
    except ImportError:
        raise SystemExit(
            "Pillow is needed to verify and pack the rasters: "
            "uv run --with pillow python packaging/make_icons.py")
    ICONS.mkdir(exist_ok=True)
    chrome = find_chrome()

    icon_svg = ICONS / "jobscout-icon.svg"
    icon_svg.write_text(compose(), encoding="utf-8")
    print(f"  {icon_svg.relative_to(HERE.parent)}  (badge {100 - 2 * MARGIN * 100:.2f}%"
          f" of the canvas, margin {MARGIN * CANVAS:.0f}px)")

    if sys.platform == "darwin":
        print("  icns:")
        with tempfile.TemporaryDirectory() as tmp:
            made = build_icns(chrome, icon_svg, Path(tmp))
        print(f"    -> {made.relative_to(HERE.parent)}")
    else:
        print("  .icns skipped: iconutil only ships with macOS")
    print("  ico:")
    print(f"    -> {build_ico(chrome, icon_svg).relative_to(HERE.parent)}")

    png = HERE.parent / "jobscout" / "web" / "icon.png"     # Linux window icon
    render(chrome, icon_svg, 512, png)
    check(png, "512px", 512)
    print(f"    -> {png.relative_to(HERE.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

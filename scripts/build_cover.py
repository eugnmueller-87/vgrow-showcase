"""Builds docs/images/cover.webp: a screen recording of Hank's garden as the moving background, the title card on top.

The scene stays as recorded except for what a cover cannot carry: the value panel top left is painted over with the
sky of the same rows (the garden's sky has one colour per row), the corner buttons, the dialog line and the browser's
scrollbar strip are cropped away, and the sky is drawn on to the left. The sound is dropped. The card is
scripts/cover_card.html, rendered on a transparent page in Edge. The loop fades the second after its end out over its
start, so the last frame runs into the first.
The crop numbers fit the owner's recording of 06.11.2026 (2128 x 1886, the garden filling the browser window); another
recording needs its own. The first second is skipped: the mouse pointer is in it.
Usage: python scripts/build_cover.py <recording.mp4> [docs/images/cover.webp]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

import imageio_ffmpeg

REPO = Path(__file__).resolve().parent.parent
EDGE = os.environ.get("EDGE_PATH", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
CARD = REPO / "scripts" / "cover_card.html"
W, H = 1600, 660
CROP = (2112, 1500, 0, 80)  # width, height, x, y: below the corner buttons, above the dialog, left of the scrollbar
PANEL = (528, 492)  # the value panel top left, in cropped pixels
SCENE_W = 930  # the crop scaled to the cover's height
START = 1.0
MASK = (f'<body style="margin:0;background:#000"><svg width="{W}" height="{H}">'
        f'<rect width="{W}" height="{H}" rx="28" fill="#fff"/></svg></body>')


def shot(html: Path, png: Path, transparent: bool) -> None:
    """One picture of a page at the cover's size, transparent where the page draws nothing. Its own profile folder:
    with the default one, headless Edge hung until the timeout (07.11.2026)."""
    args = [EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={W},{H}",
            f"--user-data-dir={png.parent / ('edge-' + png.stem)}", f"--screenshot={png}"]
    if transparent:
        args.append("--default-background-color=00000000")
    subprocess.run([*args, html.as_uri()], check=True, capture_output=True, timeout=60)


def build(video: Path, out: Path, fps: int, length: float, fade: float, quality: int) -> None:
    cw, ch, cx, cy = CROP
    pw, ph = PANEL
    graph = (f"[0:v]fps={fps},format=rgb24,crop={cw}:{ch}:{cx}:{cy},split[c1][c2];"
             f"[c2]crop=10:{ph}:{pw}:0,scale={pw}:{ph}:flags=neighbor[sky];"
             f"[c1][sky]overlay=0:0,scale={SCENE_W}:{H}:flags=area,pad={W}:{H}:{W - SCENE_W}:0,"
             f"fillborders=left={W - SCENE_W}:mode=smear,split[m][t];"
             f"[m]trim=0:{length},setpts=PTS-STARTPTS[main];"
             f"[t]trim={length}:{length + fade},setpts=PTS-STARTPTS,format=rgba,fade=t=out:st=0:d={fade}:alpha=1[tail];"
             "[main][tail]overlay=0:0:eof_action=pass[loop];"
             "[loop][1:v]overlay=0:0:shortest=1[titled];[2:v]format=gray[corners];"
             "[titled][corners]alphamerge,format=yuva420p[out]")
    with tempfile.TemporaryDirectory() as tmp:
        card, mask, mask_page = Path(tmp) / "card.png", Path(tmp) / "mask.png", Path(tmp) / "mask.html"
        mask_page.write_text(MASK, encoding="utf-8")
        shot(CARD, card, True)
        shot(mask_page, mask, False)
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y",
                        "-ss", str(START), "-t", str(length + fade + 0.2), "-i", str(video),
                        "-loop", "1", "-t", str(length), "-i", str(card),
                        "-loop", "1", "-t", str(length), "-i", str(mask),
                        "-filter_complex", graph, "-map", "[out]", "-an",
                        "-c:v", "libwebp_anim", "-lossless", "0", "-quality", str(quality), "-compression_level", "6",
                        "-loop", "0", str(out)], check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video", type=Path)
    ap.add_argument("out", type=Path, nargs="?", default=REPO / "docs" / "images" / "cover.webp")
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--length", type=float, default=8.0, help="seconds in the loop")
    ap.add_argument("--fade", type=float, default=1.0, help="seconds of the seam crossfade")
    ap.add_argument("--quality", type=int, default=82)
    a = ap.parse_args()
    build(a.video, a.out, a.fps, a.length, a.fade, a.quality)
    print(f"{a.out}: {a.out.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()

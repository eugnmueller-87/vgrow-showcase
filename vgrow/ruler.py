"""The plant's height from the GrowCam: where the plant's tip stands on the VGrow's measuring strip.

The owner, 06.11.2026: "one of the cameras is pointing exactly at the meter, where we see how big the plant actually is".
The strip on the box's rear rail is printed in centimetres, with green blocks whose edges are known marks (read off the
owner's camera on 06.11.2026): the green "Trellis" block ends at 20 cm, the white "Topping" block runs from 20 to 25 cm, the
green "Flowering" block from 25 to 30 cm. Those three edges are the scale, found again in every picture, so a camera that
moved a little still reads right, and a camera that no longer sees them says so instead of guessing.

No language model and no image library: the Mac's cockpit has neither numpy nor Pillow, and a model cannot read a ruler.
ffmpeg (already in use for every camera picture) decodes the frame to plain RGB, the rest is ordinary Python, on a worker
thread. Two masks: the strip's pure saturated green, and the leaf green that leans to yellow. The plant is the biggest group
of leaf pixels that reaches down to the pot, and its tip is that group's top row.

What the number means: the reading on the strip behind the plant, the same thing the owner reads off the picture. The plant
stands in front of the strip, so it is an estimate ("≈"), never a measurement with a tape; a typed height wins on its day
and sets the offset for the readings after it (grow_plan.py). Nothing here switches anything.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

from .camera import ffmpeg_exe

WIDTH, HEIGHT = 1280, 720  # the frame is scaled to this; at the owner's framing 16 pixels are one centimetre
HEADER_ROWS = 60  # the camera's logo and clock
FLOWERING_TOP, FLOWERING_BOTTOM, TRELLIS_TOP = 30.0, 25.0, 20.0  # cm: the printed edges

# The strip's green, in the picture: strongly saturated and pure, red and blue about equal. Leaves lean to yellow, blue below
# red. Measured on the owner's picture at lamp level 40: strip (41,82,42) and (33,98,32), leaves (51,75,45), (32,65,17).
STRIP_G_MIN, STRIP_DOMINANCE, STRIP_PURITY = 60, 30, 0.12
BAND_MIN_PX, BAND_MAX_PX = 22, 110  # how wide the green blocks stand in the picture
BAND_COLUMN_SHARE = 0.25  # a column of the band: this share of the tallest column (the print on the blocks takes the rest)
BAND_ROW_SHARE = 0.30  # a row of a block: this share of the band is strip green
BLOCK_MIN_ROWS = 20
GAP_ROWS = 4  # a shorter break inside a block is the print on it
EDGE_RATIO = (0.7, 1.4)  # the two 5 cm blocks must look alike, or this is not the strip

# The plant's green. A pixel starts the plant at the strong margin and the plant grows over the weak one: the tip and the
# thin stem are paler than the leaves. Inside the strip's band only a yellow lean counts, the strip itself is pure green.
LEAF_STRONG, LEAF_WEAK = 6, 3
BAND_LEAF_LEAN = 5  # red over blue, in the band
BAND_MARGIN = 10  # the blocks' rim blurs a little green beside them, and a plant must not climb that to the top (06.11.2026)
LEAF_MIN_PIXELS = 250
LINK = 2  # pixels further apart than this are not one plant
TIP_ROWS_MIN_PIXELS = 2  # a row counts as the tip only with this many pixels of the plant


@dataclass(frozen=True, slots=True)
class Scale:
    """Row in the picture to centimetres on the strip: linear through the printed edges."""

    y20: float
    px_per_cm: float
    x0: int
    x1: int

    def cm(self, y: float) -> float:
        return TRELLIS_TOP + (self.y20 - y) / self.px_per_cm


@dataclass(frozen=True, slots=True)
class Reading:
    cm: float
    top_y: int
    top_x: int
    pixels: int
    scale: Scale


class NotReadable(Exception):
    """The picture does not show what a reading needs. str(self) is a key: no_strip, no_plant, out_of_frame, no_decoder."""


def decode(jpeg: bytes, width: int = WIDTH, height: int = HEIGHT) -> bytes:
    """A JPEG as RGB bytes of the given size. Raises NotReadable when ffmpeg is missing or cannot read it."""
    exe = ffmpeg_exe()
    if exe is None:
        raise NotReadable("no_decoder")
    try:
        done = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-vf", f"scale={width}:{height}",
                               "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], input=jpeg, capture_output=True,
                              timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as err:
        raise NotReadable("no_decoder") from err
    if done.returncode != 0 or len(done.stdout) != width * height * 3:
        raise NotReadable("no_decoder")
    return done.stdout


def _strip_green(r: int, g: int, b: int) -> bool:
    return (g >= STRIP_G_MIN and g - r >= STRIP_DOMINANCE and g - b >= STRIP_DOMINANCE
            and abs(r - b) <= max(8.0, g * STRIP_PURITY))


def _segments(rows: list[bool]) -> list[tuple[int, int]]:
    """Runs of True, a break of at most GAP_ROWS closed, at least BLOCK_MIN_ROWS long, as (first, last)."""
    out: list[tuple[int, int]] = []
    start, last = None, None
    for y, on in enumerate(rows):
        if not on:
            continue
        if start is None:
            start = y
        elif y - last > GAP_ROWS + 1:  # type: ignore[operator]
            out.append((start, last))  # type: ignore[arg-type]
            start = y
        last = y
    if start is not None:
        out.append((start, last))  # type: ignore[arg-type]
    return [s for s in out if s[1] - s[0] + 1 >= BLOCK_MIN_ROWS]


def find_scale(rgb: bytes, width: int = WIDTH, height: int = HEIGHT) -> Scale:
    """The strip's scale in this picture, from the edges of its blocks. NotReadable("no_strip") when they are not there."""
    counts = [0] * width
    for y in range(HEADER_ROWS, height):
        row = y * width * 3
        for x in range(width):
            at = row + x * 3
            if _strip_green(rgb[at], rgb[at + 1], rgb[at + 2]):
                counts[x] += 1
    # Every run of columns that holds a lot of strip green is a candidate for the band: the sensor in the pot is green too
    tall = max(counts)
    if tall < 2 * BLOCK_MIN_ROWS:
        raise NotReadable("no_strip")
    bands: list[tuple[int, int]] = []
    start = None
    for x, n in enumerate([*counts, 0]):
        if n >= tall * BAND_COLUMN_SHARE:
            start = x if start is None else start
        elif start is not None:
            bands.append((start, x - 1))
            start = None
    for x0, x1 in bands:
        if not BAND_MIN_PX <= x1 - x0 + 1 <= BAND_MAX_PX:
            continue
        found = _blocks_in_band(rgb, width, height, x0, x1)
        if found is not None:
            return found
    raise NotReadable("no_strip")


def _blocks_in_band(rgb: bytes, width: int, height: int, x0: int, x1: int) -> Scale | None:
    wide = x1 - x0 + 1
    rows = [False] * height
    for y in range(HEADER_ROWS, height):
        row = y * width * 3
        hits = 0
        for x in range(x0, x1 + 1):
            at = row + x * 3
            if _strip_green(rgb[at], rgb[at + 1], rgb[at + 2]):
                hits += 1
        rows[y] = hits >= wide * BAND_ROW_SHARE
    blocks = _segments(rows)
    # The upper of two neighbouring blocks is Flowering, 5 cm tall, then 5 cm of white, then the Trellis block
    for upper, lower in zip(blocks, blocks[1:], strict=False):
        block, gap = upper[1] - upper[0] + 1, lower[0] - upper[1] - 1
        if gap <= 0 or not EDGE_RATIO[0] <= gap / block <= EDGE_RATIO[1]:
            continue
        px_per_cm = (lower[0] - upper[0]) / (FLOWERING_TOP - TRELLIS_TOP)
        return Scale(float(lower[0]), px_per_cm, x0, x1)
    return None


def _leaf_mask(rgb: bytes, width: int, height: int, scale: Scale) -> bytearray:
    """2 where a pixel can start the plant, 1 where the plant can only grow over it, 0 elsewhere."""
    mask = bytearray(width * height)
    band_from, band_to = scale.x0 - BAND_MARGIN, scale.x1 + BAND_MARGIN
    for y in range(HEADER_ROWS, height):
        row = y * width
        for x in range(width):
            at = (row + x) * 3
            r, g, b = rgb[at], rgb[at + 1], rgb[at + 2]
            over_r, over_b = g - r, g - b
            if over_r < LEAF_WEAK or over_b < LEAF_WEAK or _strip_green(r, g, b):
                continue
            if band_from <= x <= band_to and r - b < BAND_LEAF_LEAN:
                continue
            mask[row + x] = 2 if over_r >= LEAF_STRONG and over_b >= LEAF_STRONG else 1
    return mask


def _groups(mask: bytearray, width: int) -> list[list[int]]:
    """Groups of mask pixels no further apart than LINK, biggest first, each as a list of indexes. A group starts only on a
    strong pixel and grows over the weak ones too."""
    near = [dy * width + dx for dy in range(-LINK, LINK + 1) for dx in range(-LINK, LINK + 1) if dx or dy]
    seen = bytearray(len(mask))
    size = len(mask)
    groups = []
    for start, value in enumerate(mask):
        if value != 2 or seen[start]:
            continue
        seen[start] = 1
        stack, group = [start], []
        while stack:
            at = stack.pop()
            group.append(at)
            column = at % width
            for step in near:
                other = at + step
                if not 0 <= other < size or seen[other] or not mask[other]:
                    continue
                if abs(other % width - column) > LINK:  # a step over the picture's edge
                    continue
                seen[other] = 1
                stack.append(other)
        if len(group) >= LEAF_MIN_PIXELS:
            groups.append(group)
    groups.sort(key=len, reverse=True)
    return groups


def read(rgb: bytes, width: int = WIDTH, height: int = HEIGHT) -> Reading:
    """The tip of the plant on the strip. NotReadable: no_strip (camera moved), no_plant, out_of_frame (tip at the top edge)."""
    scale = find_scale(rgb, width, height)
    mask = _leaf_mask(rgb, width, height, scale)
    # The plant stands in the pot, so its group reaches well below the strip's 20 cm edge; a stray patch higher up does not
    plants = [g for g in _groups(mask, width) if max(g) // width > scale.y20]
    if not plants:
        raise NotReadable("no_plant")
    plant = plants[0]
    rows: dict[int, int] = {}
    for at in plant:
        rows[at // width] = rows.get(at // width, 0) + 1
    top = min(y for y, n in rows.items() if n >= TIP_ROWS_MIN_PIXELS)
    if top <= HEADER_ROWS + 1:
        raise NotReadable("out_of_frame")
    xs = [at % width for at in plant if at // width == top]
    return Reading(round(scale.cm(top), 1), top, sum(xs) // len(xs), len(plant), scale)


def read_jpeg(jpeg: bytes) -> dict[str, Any]:
    """A photo to a plain dict for the log and the page; never raises for a picture that cannot be read."""
    try:
        reading = read(decode(jpeg))
    except NotReadable as err:
        return {"ok": False, "why": str(err)}
    return {"ok": True, "cm": reading.cm, "top_y": round(reading.top_y / HEIGHT, 4), "top_x": round(reading.top_x / WIDTH, 4),
            "pixels": reading.pixels, "px_per_cm": round(reading.scale.px_per_cm, 2),
            "y20": round(reading.scale.y20 / HEIGHT, 4), "band": [round(reading.scale.x0 / WIDTH, 4),
                                                                  round(reading.scale.x1 / WIDTH, 4)]}

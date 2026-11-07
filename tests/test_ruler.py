"""The strip reader (vgrow/ruler.py): the printed edges are the scale, the plant's tip is the reading, and a picture that does
not show them says so instead of guessing. One test reads the owner's real picture."""

from __future__ import annotations

from pathlib import Path

import pytest

from vgrow import ruler

W, H = ruler.WIDTH, ruler.HEIGHT
WALL = (145, 145, 147)
STRIP_GREEN = (40, 95, 40)
STRIP_WHITE = (170, 172, 170)
LEAF = (45, 75, 30)
PX_PER_CM = 16.0


class Picture:
    def __init__(self, *, band: tuple[int, int] | None = (618, 662), y30: int = 124) -> None:
        self.px = bytearray(bytes(WALL) * (W * H))
        if band:
            x0, x1 = band
            y25, y20 = y30 + round(5 * PX_PER_CM), y30 + round(10 * PX_PER_CM)
            self.rect(x0, y30 - 60, x1, y30, STRIP_WHITE)
            self.rect(x0, y30, x1, y25, STRIP_GREEN)
            self.rect(x0, y25, x1, y20, STRIP_WHITE)
            self.rect(x0, y20, x1, H - 200, STRIP_GREEN)
            self.y20 = y20

    def rect(self, x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
        for y in range(y0, y1):
            row = y * W
            for x in range(x0, x1):
                self.px[(row + x) * 3:(row + x) * 3 + 3] = bytes(color)

    def plant(self, tip_y: int, x: int = 720, width: int = 14) -> None:
        self.rect(x - width, tip_y, x + width, H - 20, LEAF)

    def rgb(self) -> bytes:
        return bytes(self.px)


def expected(tip_y: int, y20: int) -> float:
    return 20 + (y20 - tip_y) / PX_PER_CM


def test_the_printed_edges_make_the_scale_and_the_tip_is_the_reading() -> None:
    pic = Picture()
    pic.plant(340)
    reading = ruler.read(pic.rgb())
    assert reading.scale.px_per_cm == pytest.approx(PX_PER_CM, abs=0.3)
    assert reading.cm == pytest.approx(expected(340, pic.y20), abs=0.3)
    assert reading.top_x == pytest.approx(720, abs=20)


def test_a_camera_that_moved_a_little_still_reads_right() -> None:
    pic = Picture(band=(560, 604), y30=170)
    pic.plant(400, x=700)
    assert ruler.read(pic.rgb()).cm == pytest.approx(expected(400, pic.y20), abs=0.3)


def test_no_strip_in_the_picture_says_so() -> None:
    pic = Picture(band=None)
    pic.plant(340)
    with pytest.raises(ruler.NotReadable, match="no_strip"):
        ruler.read(pic.rgb())


def test_a_green_patch_alone_is_not_the_strip() -> None:
    """The soil sensor is as green as the strip, but it has no second block at the right distance."""
    pic = Picture(band=None)
    pic.rect(500, 560, 560, 680, STRIP_GREEN)
    with pytest.raises(ruler.NotReadable, match="no_strip"):
        ruler.read(pic.rgb())


def test_no_plant_says_so_and_the_strip_is_never_taken_for_one() -> None:
    with pytest.raises(ruler.NotReadable, match="no_plant"):
        ruler.read(Picture().rgb())


def test_a_tip_above_the_picture_says_so_instead_of_reading_the_edge() -> None:
    pic = Picture()
    pic.plant(ruler.HEADER_ROWS)
    with pytest.raises(ruler.NotReadable, match="out_of_frame"):
        ruler.read(pic.rgb())


def test_a_leaf_in_front_of_the_strip_counts_and_the_strip_around_it_does_not() -> None:
    """Leaves lean to yellow, the strip is pure green: the tip can stand right in front of the blocks."""
    pic = Picture()
    pic.plant(330, x=640, width=10)
    assert ruler.read(pic.rgb()).cm == pytest.approx(expected(330, pic.y20), abs=0.4)


def test_a_small_stray_patch_is_not_the_plant() -> None:
    pic = Picture()
    pic.plant(400)
    pic.rect(900, 150, 906, 156, LEAF)
    assert ruler.read(pic.rgb()).cm == pytest.approx(expected(400, pic.y20), abs=0.3)


def test_the_owners_picture_reads_like_the_owner_reads_it() -> None:
    """06.11.2026, 10:04: the tip stands between the 10 and the 20 on the strip, about 16 cm, and the blocks are 16 px/cm."""
    jpeg = (Path(__file__).parent / "fixtures" / "growcam-strip.jpg").read_bytes()
    result = ruler.read_jpeg(jpeg)
    assert result["ok"] is True
    assert result["cm"] == pytest.approx(16.2, abs=0.6)
    assert result["px_per_cm"] == pytest.approx(15.9, abs=0.3)


def test_the_blurred_edge_of_the_strip_is_no_way_up() -> None:
    """06.11.2026, 11:34, lamp at 80 % on the veg spectrum: the first version climbed the strip's right edge from a leaf in
    front of it and read 18.3 cm at x 666. The tip stands to the right of the strip, at about 16 cm."""
    jpeg = (Path(__file__).parent / "fixtures" / "growcam-strip-80.jpg").read_bytes()
    result = ruler.read_jpeg(jpeg)
    assert result["ok"] is True
    assert result["cm"] == pytest.approx(16.2, abs=1.0)
    assert result["top_x"] * ruler.WIDTH > 700


def test_something_that_is_no_picture_is_an_answer_not_a_crash() -> None:
    assert ruler.read_jpeg(b"not a picture") == {"ok": False, "why": "no_decoder"}

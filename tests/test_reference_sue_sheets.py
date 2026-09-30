"""Tests for reference/sue_sheets.py — reading utility call-outs off SUE survey drawings.

Pure pairing/chainage logic is tested on hand-built word lists, so each rule is pinned on its
own. One test then runs the whole thing on a real delivered sheet, with the expected call-outs
read off the rendered drawing by eye — the check that the rules add up to the right answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reference import sue_sheets


def w(text: str, x0: float, top: float, width: float = 12.0, upright: bool = True) -> dict:
    return {"text": text, "x0": x0, "x1": x0 + width, "top": top, "bottom": top + 4.0, "upright": upright}


PAGE = {"width": 842.0, "height": 595.0}


def callouts(words: list[dict]) -> list[tuple[str, float | None]]:
    return [(c.utility, c.depth_m) for c in sue_sheets.callouts_from_words(words, **PAGE)]


def test_a_type_is_paired_with_the_depth_line_directly_beneath_it() -> None:
    words = [w("SEWER", 100, 80), w("Depth", 97, 85), w("1.35", 110, 85),
             w("UC", 140, 81), w("Depth", 133, 86), w("0.27m", 146, 86)]
    assert callouts(words) == [("SEWER", 1.35), ("UC", 0.27)]


def test_depth_written_with_an_equals_sign_is_read() -> None:
    assert callouts([w("DRAIN", 100, 80), w("Depth=0.70m", 100, 85)]) == [("DRAIN", 0.70)]
    assert callouts([w("Ofc", 120, 80), w("Depth=", 101, 85), w("0.60m", 115, 85)]) == [("OFC", 0.60)]


def test_storm_water_is_one_utility_not_a_storm_and_a_water() -> None:
    words = [w("STORM", 100, 80, 17), w("WATER", 118, 80, 18), w("Depth", 109, 85), w("0.64", 122, 85)]
    assert callouts(words) == [("STORM WATER", 0.64)]


def test_a_type_with_no_depth_line_is_not_a_callout() -> None:
    # Chamber and pole labels name a utility without giving a depth; they are not detections.
    assert callouts([w("WATER", 100, 80), w("Chamber", 100, 85)]) == []


def test_a_depth_is_never_borrowed_from_a_neighbouring_label() -> None:
    # The first prototype paired by nearest word and gave DRAIN its neighbour ELECTRIC's depth.
    words = [w("DRAIN", 100, 80), w("Depth=0.70m", 100, 85),
             w("ELECTRIC", 102, 97, 20), w("Depth", 104, 102), w("0.64", 117, 102)]
    assert callouts(words) == [("DRAIN", 0.70), ("ELECTRIC", 0.64)]


def test_only_the_plan_half_is_read_so_nothing_is_counted_twice() -> None:
    # The long section repeats every plan label; the plan is the half holding the CH ticks.
    words = [w("CH-20", 250, 140), w("CH-40", 420, 140),
             w("UC", 300, 80), w("Depth", 295, 85), w("0.77m", 308, 85),
             w("UC", 300, 400), w("Depth", 295, 405), w("0.77m", 308, 405)]
    assert len(callouts(words)) == 1


def test_a_depth_line_is_not_reused_once_a_closer_name_has_claimed_it() -> None:
    # Only one depth line here; without removing a claimed line from the free pool, both
    # UC (closer, claims it first) and SEWER below it would wrongly match the same line.
    words = [w("UC", 100, 80), w("SEWER", 100, 82), w("Depth", 97, 87), w("1.20", 110, 87)]
    assert callouts(words) == [("UC", 1.20)]


def test_the_legend_column_is_ignored() -> None:
    words = [w("LEGEND", 700, 30, 30), w("UC", 710, 80), w("Depth", 705, 85), w("0.10m", 718, 85)]
    assert callouts(words) == []


def test_chainage_comes_from_the_ticks_whichever_way_the_page_is_rotated() -> None:
    upright = [w("CH-20", 250, 140), w("CH-40", 418, 140), w("CH-60", 586, 140)]
    rotated = [w("02-HC", 250, 140, upright=False), w("04-HC", 418, 140, upright=False),
               w("06-HC", 586, 140, upright=False)]
    for ticks in (upright, rotated):
        words = [*ticks, w("UC", 334, 80), w("Depth", 329, 85), w("0.77m", 342, 85)]  # UC centred on 30 m
        (c,) = sue_sheets.callouts_from_words(words, **PAGE)
        assert c.chainage_m == pytest.approx(30.0, abs=0.5)


def test_without_two_ticks_chainage_is_unknown_not_invented() -> None:
    words = [w("CH-20", 250, 140), w("UC", 300, 80), w("Depth", 295, 85), w("0.77m", 308, 85)]
    (c,) = sue_sheets.callouts_from_words(words, **PAGE)
    assert c.chainage_m is None


_REAL = Path("Dataset/DSU_GPR_Files/GPR_24AUG2026/Sky group File dwg/07-03-2022 Ulsoor Road site-2LHS- 610 m.pdf")


@pytest.mark.skipif(not _REAL.exists(), reason="SUE drawings not present")
def test_a_real_sheet_reads_as_it_does_by_eye() -> None:
    sheet = sue_sheets.read_pdf(_REAL)[0]
    got = sorted((c.utility, c.depth_m) for c in sheet.callouts)
    # Read off the rendered plan of sheet 01/10 of Ulsoor Road site-2.
    assert got == sorted([
        ("DRAIN", 0.70), ("ELECTRIC", 0.64), ("SEWER", 1.35), ("UC", 0.27), ("UC", 0.70),
        ("UC", 0.96), ("UC", 0.77), ("UC", 0.77), ("WATER", 0.52), ("WATER", 1.04),
        ("OFC", 0.60), ("ELECTRIC", 0.66), ("ELECTRIC", 0.20), ("ELECTRIC", 0.66),
        ("ELECTRIC", 0.56), ("STORM WATER", 0.64),
    ])
    chainages = [c.chainage_m for c in sheet.callouts]
    assert all(ch is not None and -5 <= ch <= 65 for ch in chainages)  # one 60 m sheet


SECTION = [w("000/0-HC", 88, 540, upright=False), w("020/0-HC", 256, 540, upright=False),
           w("040/0-HC", 424, 540, upright=False), w("060/0-HC", 592, 540, upright=False)]


def test_without_plan_ticks_the_long_section_and_sheet_order_place_the_callout() -> None:
    # Section labels at 94, 262, … (centres) → 8.4 pt/m; UC centred at 178 is 10 m into the sheet.
    words = [*SECTION, w("UC", 172, 80), w("Depth", 167, 85), w("0.77m", 180, 85)]
    (c,) = sue_sheets.callouts_from_words(words, **PAGE, sheet_start_m=120.0)
    assert c.chainage_m == pytest.approx(130.0, abs=0.1)
    assert c.chainage_source == "sheet_order"


def test_plan_ticks_win_over_sheet_order_when_both_exist() -> None:
    words = [*SECTION, w("CH-80", 250, 140), w("CH-100", 418, 140),
             w("UC", 334, 80), w("Depth", 329, 85), w("0.77m", 342, 85)]
    (c,) = sue_sheets.callouts_from_words(words, **PAGE, sheet_start_m=0.0)
    assert (c.chainage_m, c.chainage_source) == (pytest.approx(90.0, abs=0.5), "plan_ticks")


def test_the_long_section_labels_mark_which_half_is_the_plan() -> None:
    # No plan ticks at all: the section labels (bottom) still say the plan is the top half.
    words = [*SECTION, w("UC", 300, 80), w("Depth", 295, 85), w("0.77m", 308, 85),
             w("UC", 300, 400), w("Depth", 295, 405), w("0.77m", 308, 405)]
    assert len(callouts(words)) == 1


def test_a_short_last_sheet_is_scaled_from_its_first_two_section_labels_only() -> None:
    # The last label on a road's final sheet is its end chainage, not another 20 m step.
    short = [*SECTION[:2], w("031/0-HC", 340, 540, upright=False)]
    words = [*short, w("UC", 172, 80), w("Depth", 167, 85), w("0.77m", 180, 85)]
    (c,) = sue_sheets.callouts_from_words(words, **PAGE, sheet_start_m=0.0)
    assert c.chainage_m == pytest.approx(10.0, abs=0.1)


def test_a_number_glued_to_the_next_word_is_still_read() -> None:
    assert callouts([w("UC", 100, 80), w("Depth", 97, 85), w("0.60mSTORM", 110, 85, 25)]) == [("UC", 0.60)]


def test_a_malformed_depth_is_left_unread_not_repaired() -> None:
    # The vendor typed "0..44" once; 0.44 is a likely reading but not what the sheet says.
    assert callouts([w("DRAIN", 100, 80), w("Depth", 97, 85), w("0..44", 110, 85)]) == [("DRAIN", None)]


def test_the_safe_path_depth_is_read_and_is_not_a_utility() -> None:
    words = [*SECTION, w("Safe", 514, 79), w("Path", 533, 79), w("Depth", 514, 88.8), w("2.00m", 538, 88.8)]
    assert sue_sheets.safe_path_from_words(words, PAGE["height"]) == 2.00
    assert callouts(words) == []


def test_geo_points_pair_each_lat_with_the_long_beneath_it() -> None:
    words = [*SECTION, w("Lat12.974647", 49, 186, 30), w("Long77.620113", 45, 191.7, 32),
             w("Lat12.974747", 603, 181, 30), w("Long77.619570", 603, 186.6, 32)]
    got = [(g.lat, g.lon) for g in sue_sheets.geo_from_words(words, PAGE["height"], sheet_start_m=0.0)]
    assert got == [(12.974647, 77.620113), (12.974747, 77.619570)]


@pytest.mark.skipif(not _REAL.exists(), reason="SUE drawings not present")
def test_a_real_sheet_gives_its_safe_path_and_both_geo_points() -> None:
    sheet = sue_sheets.read_pdf(_REAL)[0]
    assert sheet.safe_path_m == 2.00
    assert [(g.lat, g.lon) for g in sheet.geo] == [(12.974647, 77.620113), (12.974747, 77.619570)]


def test_a_safe_path_stated_only_on_the_long_section_is_still_read() -> None:
    words = [*SECTION, w("Safe", 345, 396), w("Path", 356, 396), w("Depth", 344, 401), w("2.10m", 358, 401)]
    assert sue_sheets.safe_path_from_words(words, PAGE["height"]) == 2.10


def test_lat_and_long_printed_apart_from_their_numbers_are_joined() -> None:
    words = [*SECTION, w("Lat", 40, 202.8, 8), w("12.973878", 49, 202.8, 22),
             w("Long77.595482", 37.4, 208.4, 32)]
    got = [(g.lat, g.lon) for g in sue_sheets.geo_from_words(words, PAGE["height"])]
    assert got == [(12.973878, 77.595482)]


def test_a_depth_word_glued_to_the_previous_label_is_still_a_depth_line() -> None:
    words = [w("UC", 100, 80), w("0.36mDepth", 90, 85, 20), w("0.70m", 112, 85),
             w("Ofc", 200, 80), w("mDepth=", 190, 85, 20), w("0.80m", 212, 85)]
    assert callouts(words) == [("UC", 0.70), ("OFC", 0.80)]


def test_a_plan_running_past_the_middle_of_the_page_is_read_down_to_the_long_section_heading() -> None:
    words = [*SECTION, w("L-SECTION", 314, 367, 30),
             w("UC", 396, 316), w("Depth", 396, 323), w("1.12m", 413, 323),
             w("UC", 396, 400), w("Depth", 396, 405), w("1.12m", 413, 405)]
    assert callouts(words) == [("UC", 1.12)]


def test_a_sheet_with_no_labels_at_all_does_not_count_the_long_sections_repeats() -> None:
    words = [w("ELECTRIC", 100, 50), w("Depth=0.64m", 100, 58),
             w("ELECTRIC", 100, 500), w("Depth=0.64m", 100, 508)]
    assert callouts(words) == [("ELECTRIC", 0.64)]


def test_a_same_named_callout_far_off_in_x_is_not_deduped_as_a_repeat() -> None:
    # Two genuinely different ELECTRIC call-outs, both on an unlocated (whole-drawing) read, 200
    # pt apart in x: too far apart to be the long section's repeat of the same plan label.
    words = [w("ELECTRIC", 100, 50), w("Depth=0.64m", 100, 58),
             w("ELECTRIC", 300, 500), w("Depth=0.64m", 300, 508)]
    assert callouts(words) == [("ELECTRIC", 0.64), ("ELECTRIC", 0.64)]


def test_a_same_named_repeat_with_a_different_depth_is_not_deduped() -> None:
    # Same utility name, close in x, but a different depth reading: two distinct call-outs, not
    # a repeat of the same one (an actual repeat always carries the same depth as its original).
    words = [w("ELECTRIC", 100, 50), w("Depth=0.64m", 100, 58),
             w("ELECTRIC", 102, 500), w("Depth=0.80m", 102, 508)]
    assert callouts(words) == [("ELECTRIC", 0.64), ("ELECTRIC", 0.80)]


def test_with_no_labels_the_long_section_heading_alone_locates_the_plan() -> None:
    words = [w("L-SECTION", 314, 367, 30), w("UC", 300, 80), w("Depth", 295, 85), w("0.40m", 308, 85),
             w("UC", 500, 400), w("Depth", 495, 405), w("0.90m", 508, 405)]
    assert callouts(words) == [("UC", 0.40)]


def test_pipe_line_is_one_utility_like_storm_water() -> None:
    words = [w("Pipe", 100, 80, 12), w("Line", 113, 80, 12), w("Depth=1.50m", 100, 85, 25)]
    assert callouts(words) == [("PIPE LINE", 1.50)]


def test_a_letter_spaced_u_c_is_read_as_uc() -> None:
    words = [w("U", 530, 53, 2.5), w("C", 533, 53, 2.5), w("Depth", 512, 58), w("1.00", 524, 58)]
    assert callouts(words) == [("UC", 1.00)]


def test_a_two_word_name_too_far_apart_does_not_merge() -> None:
    # "STORM" and "WATER" 40 pt apart on the same line are two separate, unrelated labels, not
    # one utility — merging is only for words that sit side by side.
    words = [w("STORM", 100, 80, 17), w("WATER", 158, 80, 18), w("Depth", 109, 85), w("0.64", 122, 85)]
    assert callouts(words) == []


def test_one_plan_tick_is_anchored_with_the_long_section_scale() -> None:
    # Section labels give 8.4 pt/m; CH-100 centred at 262, UC centred at 178 is 10 m before it.
    words = [*SECTION, w("CH-100", 256, 140), w("UC", 172, 80), w("Depth", 167, 85), w("0.77m", 180, 85)]
    (c,) = sue_sheets.callouts_from_words(words, **PAGE)
    assert (c.chainage_m, c.chainage_source) == (pytest.approx(90.0, abs=0.1), "plan_ticks")


def test_page_order_is_trusted_only_when_every_checkable_page_agrees() -> None:
    assert sue_sheets.order_is_chainage([[0, 20], [], [140, 160], [200], []])
    # Rajbhavan Road's PDF runs sheet 1, then 18 down to 2: its second page reads CH-1040.
    assert not sue_sheets.order_is_chainage([[0, 20], [1040, 1060], [140], [200]])
    assert not sue_sheets.order_is_chainage([[], []])  # nothing to check it against
    # Two readable pages out of five can't vouch for the other three.
    assert not sue_sheets.order_is_chainage([[0, 20], [], [140], [], []])
    # A road's last sheet may run long: Ulsoor Road site-2 ends at CH-610 on its 10th page.
    assert sue_sheets.order_is_chainage([[0], [80], *[[]] * 7, [560, 610]])
    assert not sue_sheets.order_is_chainage([[0], [80], [610], [], []])


def test_pages_out_of_order_get_no_chainage_from_their_position() -> None:
    page_with_ticks = [*SECTION, w("CH-1040", 256, 140), w("CH-1060", 424, 140)]
    page_without = [*SECTION, w("UC", 172, 80), w("Depth", 167, 85), w("0.77m", 180, 85)]
    sheets = sue_sheets.read_pages([(page_with_ticks, 842.0, 595.0), (page_without, 842.0, 595.0)])
    (c,) = sheets[1].callouts
    assert (c.chainage_m, c.chainage_source) == (None, "unavailable")


def test_two_section_labels_on_one_spot_give_no_scale_rather_than_a_crash() -> None:
    # OCR produced exactly this on a real sheet, and the chainage fit divided by zero.
    words = [w("000/0-HC", 88, 540, upright=False), w("020/0-HC", 88, 540, upright=False),
             w("CH-100", 256, 140), w("UC", 172, 80), w("Depth", 167, 85), w("0.77m", 180, 85)]
    (c,) = sue_sheets.callouts_from_words(words, **PAGE)
    assert (c.chainage_m, c.chainage_source) == (None, "unavailable")

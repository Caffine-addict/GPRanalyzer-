"""Reading utility call-outs off the vendor's SUE (subsurface utility engineering) sheets.

Each sheet covers 60 m of road: a plan view with chainage ticks (CH-00 … CH-60) and a long
section below it that repeats every plan label. A call-out is a utility name ("ELECTRIC",
"UC", "STORM WATER", "Ofc", …) with a "Depth 0.64" line directly beneath it — depth to the
top of the utility, as the vendor measured it (their sheets state ±30%).

Only the plan half is read, so each utility is counted once. Chainage comes from a
straight-line fit through the plan's own tick labels where at least two can be read
("plan_ticks"). Many sheets draw those ticks as linework, not text; there the long section's
local labels (CH-0/000, CH-0/020 … — present on every sheet) give the scale, and the sheet's
position in the PDF gives the offset, 60 m per sheet ("sheet_order"). The title block is no
help: it reads "01/10" on every delivered sheet. On the 8 delivered road PDFs, 179 of 182
readable plan ticks agree with sheet order within 2 m; the 3 that don't are on one road's
short final sheet. Either way the chainage is that of the *label*, which the draughtsman puts
beside the utility, so it is good to a few metres, not to the centimetre. With neither
source, chainage is None, never a guess.

Pure functions over pdfplumber word dicts, so the rules are testable without a PDF;
`read_pdf` is the one place a file is opened.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

UTILITIES = ("ELECTRIC", "WATER", "SEWER", "DRAIN", "UC", "OFC", "METALLIC", "GAS", "TELECOM")
_TICK = re.compile(r"^CH-(\d{2,4})$")
_SECTION_TICK = re.compile(r"^CH-\d+/\d+$")
SHEET_LENGTH_M = 60.0
_SECTION_TICK_SPACING_M = 20.0
# Searched, not matched: the text layer sometimes glues the previous label's tail on
# ("0.36mDepth", "mDepth="), and the depth line is still this word.
_DEPTH = re.compile(r"(?:^|[\dm])Depth=?(\d+(?:\.\d+)?)?m?$", re.IGNORECASE)
# A trailing word is sometimes glued on in the PDF's text layer: "0.36mDepth", "0.60mSTORM".
_NUMBER = re.compile(r"^(\d+(?:\.\d+)?)m?(?:[A-Za-z]+)?$")

# Word-box geometry, in PDF points, measured on the delivered sheets: the depth line sits
# ~5 pt below its utility name, and the number follows "Depth" on the same baseline.
_LINE_GAP = (2.5, 8.0)
_SAME_LINE = 1.5
_NUMBER_REACH = 15.0
_REPEAT_X = 5.0  # a long-section repeat sits within this many points of its plan label's x
_SAFE_PATH_GAP = (3.0, 18.0)  # "Safe Path" sits above its own "Depth 2.00m" line, left-aligned with "Safe"
_LAT = re.compile(r"^Lat(\d{1,2}\.\d+)$")
_LON = re.compile(r"^Long(\d{1,3}\.\d+)$")

Word = dict[str, Any]


@dataclass(frozen=True)
class Callout:
    utility: str
    depth_m: float | None
    chainage_m: float | None  # along the road
    chainage_source: str  # "plan_ticks", "sheet_order" or "unavailable"
    box: tuple[float, float, float, float]  # x0, top, x1, bottom of name + depth line


@dataclass(frozen=True)
class GeoPoint:
    """A Lat/Long the vendor printed on the plan — where a sheet starts or ends."""

    lat: float
    lon: float
    chainage_m: float | None  # of the label, which sits just beside its point


@dataclass(frozen=True)
class Sheet:
    page: int  # 1-based
    callouts: tuple[Callout, ...]
    safe_path_m: float | None  # the HDD bore depth the vendor recommends for this sheet
    geo: tuple[GeoPoint, ...]


def _tick_value(word: Word) -> int | None:
    # Upside-down labels come out of the PDF reversed: "02-HC" is CH-20.
    for text in (word["text"], word["text"][::-1]):
        if m := _TICK.match(text):
            return int(m.group(1))
    return None


def is_tick_label(text: str) -> bool:
    """A plan tick ("CH-20") or long-section label ("CH-0/020"), either way round."""
    return any(_TICK.match(t) or _SECTION_TICK.match(t) for t in (text, text[::-1]))


def _ticks(words: Sequence[Word]) -> list[tuple[float, int, float]]:
    """(x centre, chainage, top) of every plan chainage tick label."""
    return [((w["x0"] + w["x1"]) / 2, v, w["top"]) for w in words if (v := _tick_value(w)) is not None]


def _chainage_fit(ticks: list[tuple[float, int, float]]) -> tuple[float, float] | None:
    """Least-squares metres = a*x + b, or None without two distinct ticks."""
    if len({v for _, v, _ in ticks}) < 2:
        return None
    n = len(ticks)
    mx = sum(x for x, _, _ in ticks) / n
    mv = sum(v for _, v, _ in ticks) / n
    sxx = sum((x - mx) ** 2 for x, _, _ in ticks)
    if sxx == 0:
        return None
    a = sum((x - mx) * (v - mv) for x, v, _ in ticks) / sxx
    return a, mv - a * mx


def _section_ticks(words: Sequence[Word]) -> list[Word]:
    """The long section's local labels (CH-0/000 …), written upside down or not."""
    return sorted((w for w in words if _SECTION_TICK.match(w["text"]) or _SECTION_TICK.match(w["text"][::-1])),
                  key=lambda w: w["x0"])


def _section_scale(words: Sequence[Word]) -> tuple[float, float] | None:
    """(x of the sheet's 0 m, points per metre) from the first two long-section labels, 20 m apart.

    Only the first two: a road's short last sheet ends on an extra label that is not 20 m on.
    """
    labels = _section_ticks(words)
    if len(labels) < 2:
        return None
    x0, x1 = ((w["x0"] + w["x1"]) / 2 for w in labels[:2])
    if abs(x1 - x0) < 1.0:  # two labels on one spot (a misread or duplicate) give no scale
        return None
    return x0, (x1 - x0) / _SECTION_TICK_SPACING_M


# Utility names printed as two words, merged when they sit side by side on one line.
# "U" "C" is how the text layer letter-spaces UC on some sheets.
_TWO_WORD_NAMES = {("STORM", "WATER"): "STORM WATER", ("PIPE", "LINE"): "PIPE LINE", ("U", "C"): "UC"}


def _names(words: Sequence[Word]) -> list[tuple[str, Word]]:
    """Utility names with their boxes; two-word names ("STORM WATER", "Pipe Line") merge into one."""
    out: list[tuple[str, Word]] = []
    used: set[int] = set()
    for i, w in enumerate(words):
        if i in used:
            continue
        text = w["text"].upper()
        seconds = {second: name for (first, second), name in _TWO_WORD_NAMES.items() if first == text}
        if seconds:
            partner = next((j for j, o in enumerate(words) if j != i and o["text"].upper() in seconds
                            and abs(o["top"] - w["top"]) <= _SAME_LINE and 0 <= o["x0"] - w["x1"] <= 6), None)
            if partner is not None:
                used.add(partner)
                o = words[partner]
                out.append((seconds[o["text"].upper()],
                            {**w, "x1": o["x1"], "bottom": max(w["bottom"], o["bottom"])}))
        elif text in UTILITIES:
            out.append((text, w))
    return [(name, box) for name, box in out if not any(box is words[j] for j in used)]


def _depth_lines(words: Sequence[Word]) -> list[tuple[Word, float | None]]:
    """Each "Depth …" line as one box, with its number when one can be read."""
    lines: list[tuple[Word, float | None]] = []
    for w in words:
        m = _DEPTH.search(w["text"])
        if not m:
            continue
        if m.group(1):
            lines.append((w, float(m.group(1))))
            continue
        number = next((o for o in words if abs(o["top"] - w["top"]) <= _SAME_LINE
                       and 0 <= o["x0"] - w["x1"] <= _NUMBER_REACH and _NUMBER.match(o["text"])), None)
        if number is None:
            lines.append((w, None))
        else:
            value = float(_NUMBER.match(number["text"]).group(1))  # type: ignore[union-attr]
            lines.append(({**w, "x1": number["x1"], "bottom": max(w["bottom"], number["bottom"])}, value))
    return lines


def _plan_split(words: Sequence[Word], height: float) -> tuple[list[Word], bool]:
    """The plan's words, and whether the plan could be told apart from the long section.

    The legend/title-block column is always dropped. The plan is located by the long section's
    labels, else the plan's own ticks, else the "L-SECTION" heading it sits above; with none of
    those the whole drawing comes back, flagged, so the caller can guard against the long
    section's repeats.
    """
    kept = _drawing_words(words)
    # The plan can run past the middle of the page; the "L-SECTION" heading is where it stops.
    heading = next((w for w in kept if w["text"] == "L-SECTION" and w["top"] > height / 3), None)
    if section := _section_ticks(kept):
        plan_is_top = sum(w["top"] for w in section) / len(section) >= height / 2
    elif ticks := _ticks(kept):
        plan_is_top = sum(top for _, _, top in ticks) / len(ticks) < height / 2
    elif heading is not None:
        plan_is_top = True
    else:
        return kept, False
    split = heading["top"] if plan_is_top and heading is not None else height / 2
    return [w for w in kept if (w["top"] < split) == plan_is_top], True


def _plan_words(words: Sequence[Word], height: float) -> list[Word]:
    return _plan_split(words, height)[0]


def _chainage(words: Sequence[Word], plan: Sequence[Word],
              sheet_start_m: float | None) -> tuple[tuple[float, float] | None, str]:
    """(metres = a*x + b, source), preferring the plan's own ticks over sheet order.

    Two plan ticks fix the line on their own; a single one fixes it with the long section's
    scale. Only with no plan tick at all does the sheet's position in the PDF come in.
    """
    ticks = _ticks(plan)
    if (fit := _chainage_fit(ticks)) is not None:
        return fit, "plan_ticks"
    scale = _section_scale(words)
    if scale is not None and ticks:
        x, value, _ = ticks[0]
        per_m = scale[1]
        return (1 / per_m, value - x / per_m), "plan_ticks"
    if scale is not None and sheet_start_m is not None:
        origin, per_m = scale
        return (1 / per_m, sheet_start_m - origin / per_m), "sheet_order"
    return None, "unavailable"


def _at(fit: tuple[float, float] | None, x: float) -> float | None:
    return None if fit is None else round(fit[0] * x + fit[1], 1)


def callouts_from_words(words: Sequence[Word], width: float, height: float,
                        sheet_start_m: float | None = None) -> list[Callout]:
    """Every plan call-out on one page, in reading order.

    `sheet_start_m` is where this sheet starts along the road, when known from outside the
    page (its order in the PDF); it is used only when the plan's own ticks can't be read.
    `width` is accepted for symmetry with `height`.
    """
    del width
    plan, located = _plan_split(words, height)
    fit, source = _chainage(words, plan, sheet_start_m)
    free = list(_depth_lines(plan))
    found = []
    for name, box in sorted(_names(plan), key=lambda nb: (nb[1]["top"], nb[1]["x0"])):
        below = [(line, value) for line, value in free
                 if _LINE_GAP[0] <= line["top"] - box["top"] <= _LINE_GAP[1]
                 and line["x0"] < box["x1"] and box["x0"] < line["x1"]]
        if not below:
            continue
        line, value = min(below, key=lambda lv: lv[0]["top"] - box["top"])
        free.remove((line, value))
        centre = (box["x0"] + box["x1"]) / 2
        found.append(Callout(
            utility=name,
            depth_m=value,
            chainage_m=_at(fit, centre),
            chainage_source="unavailable" if fit is None else source,
            box=(min(box["x0"], line["x0"]), box["top"], max(box["x1"], line["x1"]), line["bottom"]),
        ))
    if not located:
        # Plan and long section were read together; the section repeats a plan label at the
        # same x with the same type and depth, so keep only the first of each such pair.
        found = [c for k, c in enumerate(found) if not any(
            o.utility == c.utility and o.depth_m == c.depth_m and abs(o.box[0] - c.box[0]) <= _REPEAT_X
            for o in found[:k])]
    return found


def _drawing_words(words: Sequence[Word]) -> list[Word]:
    """Plan and long section together — everything left of the legend column."""
    legend = next((w for w in words if w["text"].upper() == "LEGEND"), None)
    return [w for w in words if legend is None or w["x1"] < legend["x0"] - 10]


def safe_path_from_words(words: Sequence[Word], height: float) -> float | None:
    """The "Recommended Safe Path / Depth 2.00m" note, or None when the sheet doesn't state one.

    Some sheets print it on the plan and the long section, some only on the long section.
    """
    del height
    drawing = _drawing_words(words)
    lines = _depth_lines(drawing)
    for safe_word in sorted((w for w in drawing if w["text"] == "Safe"), key=lambda w: w["top"]):
        for line, value in lines:
            if value is not None and _SAFE_PATH_GAP[0] <= line["top"] - safe_word["top"] <= _SAFE_PATH_GAP[1] \
                    and abs(line["x0"] - safe_word["x0"]) <= 25:
                return value
    return None


def _with_following(words: Sequence[Word], word: Word) -> str:
    """ "Lat" and "12.97…" printed as two words, joined; any other word unchanged."""
    if word["text"] not in ("Lat", "Long"):
        return str(word["text"])
    after = next((o for o in words if abs(o["top"] - word["top"]) <= _SAME_LINE
                  and 0 <= o["x0"] - word["x1"] <= 5), None)
    return word["text"] + (after["text"] if after else "")


def geo_from_words(words: Sequence[Word], height: float,
                   sheet_start_m: float | None = None) -> list[GeoPoint]:
    """Each printed "Lat…" with the "Long…" directly beneath it, left to right."""
    plan = _plan_words(words, height)
    fit, _ = _chainage(words, plan, sheet_start_m)
    points = []
    text = {id(w): _with_following(plan, w) for w in plan}
    for lat_word in sorted((w for w in plan if _LAT.match(text[id(w)])), key=lambda w: w["x0"]):
        lon_word = next((o for o in plan if _LON.match(text[id(o)]) and 2 <= o["top"] - lat_word["top"] <= 8
                         and abs(o["x0"] - lat_word["x0"]) <= 8), None)
        if lon_word is None:
            continue
        points.append(GeoPoint(
            lat=float(_LAT.match(text[id(lat_word)]).group(1)),  # type: ignore[union-attr]
            lon=float(_LON.match(text[id(lon_word)]).group(1)),  # type: ignore[union-attr]
            chainage_m=_at(fit, (lat_word["x0"] + lat_word["x1"]) / 2),
        ))
    return points


def has_text_layer(path: Path) -> bool:
    """Whether the PDF's first page has any text to read (outlined drawings have none)."""
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return bool(pdf.pages) and bool(pdf.pages[0].chars)


_ORDER_TOLERANCE_M = 5.0  # a tick may sit this far outside its page's 60 m slot
_MIN_CHECKED_PAGES = 3


def order_is_chainage(page_ticks: Sequence[Sequence[int]]) -> bool:
    """Whether the PDF's pages run in chainage order, 60 m each, as far as their ticks can tell.

    `page_ticks` holds each page's plan tick values (empty where none could be read). Not
    something to assume: one delivered drawing (Rajbhavan Road) runs sheet 1, then 18 down to 2.
    Every page with ticks must have them inside its own 60 m slot (the last sheet may run long:
    a 610 m road ends on a 70 m sheet), and at least three pages must be checkable — or every
    page, in a drawing shorter than that — so one or two readable pages can't vouch for the rest.
    """
    last = len(page_ticks) - 1
    checked = [(i, ticks) for i, ticks in enumerate(page_ticks) if ticks]
    if len(checked) < min(_MIN_CHECKED_PAGES, len(page_ticks)) or not checked:
        return False
    return all(i * SHEET_LENGTH_M - _ORDER_TOLERANCE_M <= v
               and (i == last or v <= (i + 1) * SHEET_LENGTH_M + _ORDER_TOLERANCE_M)
               for i, ticks in checked for v in ticks)


def read_pages(pages: Sequence[tuple[list[Word], float, float]]) -> list[Sheet]:
    """Every page of one drawing, given each page's (words, width, height)."""
    in_order = order_is_chainage([[v for _, v, _ in _ticks(_plan_words(words, height))]
                                  for words, _, height in pages])
    sheets = []
    for index, (words, width, height) in enumerate(pages):
        start = index * SHEET_LENGTH_M if in_order else None
        sheets.append(Sheet(
            page=index + 1,
            callouts=tuple(callouts_from_words(words, width, height, sheet_start_m=start)),
            safe_path_m=safe_path_from_words(words, height),
            geo=tuple(geo_from_words(words, height, sheet_start_m=start)),
        ))
    return sheets


def read_pdf(path: Path) -> list[Sheet]:
    """Read every page of one SUE drawing PDF."""
    import pdfplumber  # imported here so the pure functions above need no PDF library

    with pdfplumber.open(path) as pdf:
        return read_pages([(page.extract_words(x_tolerance=1.5, y_tolerance=1), page.width, page.height)
                           for page in pdf.pages])

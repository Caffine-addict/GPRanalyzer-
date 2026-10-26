"""Site drawings (CAD): a survey company's utility map, shown in the Studio as it was drawn.

A .dwg is converted to .dxf by LibreDWG's dwg2dxf, run as a separate program (it is GPL-3.0, so it
is never imported — the same rule as gprMax). A .dxf is read with ezdxf. Every drawn entity becomes
plain line geometry or a text label in the drawing's own units and grid; nothing is moved,
reprojected or interpreted, except that utility labels such as "Drain Pipe D : ~ 0.50 m Ø ~ 254 mm
QL-A,D" are also read into type, depth, diameter and quality level so they can be listed.

Stored beside the survey data (dataset parent / site_drawings), which is never committed.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ezdxf import colors, disassemble, recover
from ezdxf import path as ezpath

from studio.imports import _base_name

SUFFIXES = {".dwg", ".dxf"}
FLATTEN_DISTANCE = 0.02  # max chord error when arcs and splines become straight segments (drawing units)
CONVERT_TIMEOUT_S = 180
_CACHE = ".geometry.json"
_FORMAT = 3  # bump when what parse() returns changes, so cached geometry is rebuilt
LABEL_REACH = 1.5  # drawing units: how close a leader end must be to its label
LINE_REACH = 0.6  # ...and its other end to the utility line it points at
JOIN_REACH = 0.05  # line pieces whose ends are this close are one drawn utility
_TOOL_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin")
_UTILITY_WORDS = re.compile(r"cable|pipe|drain|water|fire|hydrant|sewer|sewage|telecom|ofc|gas|earth|metallic|"
                            r"utility|chamber|duct|line|trench|power|electric", re.IGNORECASE)
_LABEL = re.compile(r"QL-([ABCD])")
_DEPTH = re.compile(r"D\s*:\s*~?\s*([\d.]+)\s*m")
_DIAMETER = re.compile(r"Ø\s*~?\s*(\d+)\s*mm")


def folder(dataset_dir: Path) -> Path:
    return dataset_dir.parent / "site_drawings"


def _dxf_for(path: Path) -> Path:
    return path.with_suffix(".dxf")


def listing(dataset_dir: Path) -> list[dict[str, Any]]:
    """Each imported drawing once, by name (a .dwg and the .dxf made from it are one drawing)."""
    root = folder(dataset_dir)
    if not root.exists():
        return []
    names = sorted({p.stem for p in root.iterdir() if p.suffix.lower() in SUFFIXES and not p.name.startswith(".")})
    return [{"name": name, "original": next((p.name for p in sorted(root.iterdir()) if p.stem == name
                                             and p.suffix.lower() == ".dwg"), f"{name}.dxf")} for name in names]


def _converter() -> str:
    # A background service (launchd, systemd) often runs with a bare PATH: look where package managers put it too.
    found = shutil.which("dwg2dxf", path=os.pathsep.join([os.environ.get("PATH", ""), *_TOOL_DIRS]))
    if not found:
        raise ValueError("reading a .dwg needs LibreDWG's dwg2dxf (macOS: brew install libredwg); "
                         "or import the drawing saved as .dxf")
    return found


def import_drawing(dataset_dir: Path, client_name: str, data: bytes) -> str:
    """Store a drawing (and, for a .dwg, the .dxf made from it). Returns its name. Never overwrites."""
    name = _base_name(client_name)
    suffix = Path(name).suffix.lower()
    if not name or name.startswith(".") or suffix not in SUFFIXES:
        raise ValueError(f"{client_name}: not a CAD drawing (expected .dwg or .dxf)")
    if not data:
        raise ValueError(f"{client_name} is empty")
    root = folder(dataset_dir)
    root.mkdir(parents=True, exist_ok=True)
    stem = Path(name).stem
    if any(p.stem == stem for p in root.iterdir()):
        raise ValueError(f"a drawing named {stem} is already imported")
    target = root / f"{stem}{suffix}"
    with tempfile.TemporaryDirectory(dir=root, prefix=".import-") as staging:
        staged = Path(staging) / f"drawing{suffix}"
        staged.write_bytes(data)
        if suffix == ".dwg":
            out = Path(staging) / "drawing.dxf"
            done = subprocess.run([_converter(), "-y", "-o", str(out), str(staged)], capture_output=True, text=True,
                                  timeout=CONVERT_TIMEOUT_S, check=False)
            if done.returncode != 0 or not out.exists():
                raise ValueError(f"{client_name}: dwg2dxf could not read it ({done.stderr.strip()[-200:]})")
            shutil.move(str(out), str(_dxf_for(target)))
        shutil.move(str(staged), str(target))
    geometry(dataset_dir, stem)  # parse once now: a broken drawing is reported at import, not on viewing
    return stem


def path_of(dataset_dir: Path, name: str) -> Path:
    """The drawing's .dxf, found by membership in the listing — never by joining the name."""
    for item in listing(dataset_dir):
        if item["name"] == name:
            return folder(dataset_dir) / f"{name}.dxf"
    raise FileNotFoundError(name)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def utility_label(text: str) -> dict[str, Any] | None:
    """'Drain Pipe D : ~ 0.50 m Ø ~ 254 mm QL-A,D' -> kind, depth, diameter, quality level; None if not a label."""
    level = _LABEL.search(text)
    if not level:
        return None
    depth, diameter = _DEPTH.search(text), _DIAMETER.search(text)
    kind = re.split(r"\bD\s*:|Ø|QL-", text)[0].strip(" ,") or "unlabelled"
    return {"kind": kind, "quality_level": f"QL-{level.group(1)}",
            "depth_m": float(depth.group(1)) if depth else None,
            "diameter_mm": int(diameter.group(1)) if diameter else None}


def _text(entity: Any) -> str:
    """The label's text with its line breaks kept (MTEXT paragraphs), spaces within a line collapsed."""
    raw = entity.plain_text() if entity.dxftype() == "MTEXT" else entity.dxf.text
    raw = raw.replace("%%U", "").replace("%%u", "").replace("%%c", "Ø").replace("%%C", "Ø")
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines() if line.strip())


def _anchor(entity: Any) -> tuple[float, float, Any]:
    """(horizontal 0..1 from the left, vertical 0..1 from the top, the point the text hangs from)."""
    if entity.dxftype() == "MTEXT":  # attachment point 1..9: top/middle/bottom rows of left/centre/right
        attach = int(entity.dxf.get("attachment_point", 1) or 1)
        return ((attach - 1) % 3) / 2, ((attach - 1) // 3) / 2, entity.dxf.insert
    halign, valign = int(entity.dxf.get("halign", 0) or 0), int(entity.dxf.get("valign", 0) or 0)
    point = entity.dxf.get("align_point") if (halign or valign) and entity.dxf.hasattr("align_point") else entity.dxf.insert
    horizontal = {0: 0.0, 1: 0.5, 2: 1.0, 4: 0.5}.get(halign, 0.0)
    vertical = {0: 1.0, 1: 1.0, 2: 0.5, 3: 0.0}.get(valign, 1.0)  # baseline is close enough to the bottom
    return horizontal, vertical, point


def _lines(entity: Any) -> list[list[list[float]]]:
    """An entity as one or more polylines of (x, y), or [] if it draws nothing."""
    kind = entity.dxftype()
    try:
        paths = list(ezpath.from_hatch(entity)) if kind == "HATCH" else [ezpath.make_path(entity)]
    except (TypeError, ValueError, AttributeError):
        return []
    out = []
    for path in paths:
        points = [[round(p.x, 2), round(p.y, 2)] for p in path.flattening(FLATTEN_DISTANCE)]  # centimetres
        if len(points) >= 2:
            out.append(points)
    return out


def join_segments(polylines: list[list[list[float]]]) -> list[list[list[float]]]:
    """Chain polylines that touch end to start into longer ones: the same strokes, far fewer pieces.

    Sketched symbols (trees, hatching drawn as lines) arrive as thousands of separate two-point
    LINEs; drawn one by one they make the view crawl. Joining only exact shared endpoints changes
    nothing that is drawn.
    """
    by_start: dict[tuple[float, ...], list[int]] = {}
    for i, line in enumerate(polylines):
        by_start.setdefault(tuple(line[0]), []).append(i)
    used = [False] * len(polylines)
    out = []
    for i, line in enumerate(polylines):
        if used[i]:
            continue
        used[i] = True
        chain = list(line)
        while True:
            nexts = [j for j in by_start.get(tuple(chain[-1]), []) if not used[j]]
            if not nexts:
                break
            used[nexts[0]] = True
            chain.extend(polylines[nexts[0]][1:])
        out.append(chain)
    return out


def _entities(space: Any) -> Any:
    """(primitive, is_annotation) for everything drawn, with blocks, dimensions and leaders opened up.

    Annotation (a dimension's lines, a leader's arrow) is drawn like everything else, but is never
    a utility, even when it sits on a utility's layer as leaders here do.
    """
    for entity in disassemble.recursive_decompose(space):
        if entity.dxftype() in ("DIMENSION", "LEADER", "MULTILEADER", "ACAD_TABLE"):
            try:
                yield from ((v, True) for v in getattr(entity, "virtual_entities")())  # noqa: B009
            except (TypeError, ValueError, AttributeError):
                continue
        else:
            yield entity, False


def parse(dxf_path: Path) -> dict[str, Any]:
    doc, _auditor = recover.readfile(str(dxf_path))
    layer_colour = {}
    for layer in doc.layers:
        rgb = layer.rgb if layer.rgb else colors.aci2rgb(abs(layer.color) if 0 < abs(layer.color) < 256 else 7)
        layer_colour[layer.dxf.name] = _hex(rgb)
    lines: dict[str, list[list[list[float]]]] = {}
    texts: list[dict[str, Any]] = []
    leaders: list[tuple[str, tuple[float, float], tuple[float, float]]] = []
    for leader in doc.modelspace().query("LEADER"):
        vertices = getattr(leader, "vertices", [])  # LEADER's own attribute, not on DXFEntity's type
        if len(vertices) >= 2:
            leaders.append((leader.dxf.layer, (vertices[0][0], vertices[0][1]), (vertices[-1][0], vertices[-1][1])))
    annotation: dict[str, list[list[list[float]]]] = {}
    for entity, is_annotation in _entities(doc.modelspace()):
        layer = entity.dxf.get("layer", "0")
        if entity.dxftype() in ("TEXT", "MTEXT", "ATTRIB"):
            value = _text(entity)
            if not value:
                continue
            ax, ay, insert = _anchor(entity)
            height = entity.dxf.get("char_height" if entity.dxftype() == "MTEXT" else "height", 1.0) or 1.0
            rotation = entity.dxf.get("rotation", 0.0) or 0.0
            texts.append({"layer": layer, "x": round(insert.x, 3), "y": round(insert.y, 3), "text": value,
                          "height": round(float(height), 3), "rotation": round(float(rotation), 1),
                          "ax": ax, "ay": ay, "utility": utility_label(value.replace("\n", " "))})
        else:
            (annotation if is_annotation else lines).setdefault(layer, []).extend(_lines(entity))
    lines = {layer: join_segments(polylines) for layer, polylines in lines.items()}
    depths = depth_lines(lines, texts, leaders)  # before annotation joins the drawing: arrows are not utilities
    for name, polylines in annotation.items():
        lines.setdefault(name, []).extend(join_segments(polylines))
    xs = [p[0] for polylines in lines.values() for line in polylines for p in line]
    ys = [p[1] for polylines in lines.values() for line in polylines for p in line]
    labelled = {t["layer"] for t in texts if t["utility"]}
    layers = []
    for name in sorted(set(lines) | {t["layer"] for t in texts}):
        layers.append({"name": name, "colour": layer_colour.get(name, "#9ca3af"),
                       "lines": len(lines.get(name, [])), "texts": sum(1 for t in texts if t["layer"] == name),
                       "utility": name in labelled or bool(_UTILITY_WORDS.search(name)),
                       "gpr_corridor": "gpr" in name.lower()})
    units = {0: "unitless", 1: "inches", 2: "feet", 4: "millimetres", 5: "centimetres", 6: "metres"}
    return {
        "bounds": [[min(xs), min(ys)], [max(xs), max(ys)]] if xs else None,
        "units_declared": units.get(doc.header.get("$INSUNITS", 0), f"code {doc.header.get('$INSUNITS')}"),
        "layers": layers, "lines": lines, "texts": texts,
        "utilities": [t for t in texts if t["utility"]],
        "depths": depths,
    }


def _segment_distance(p: tuple[float, float], a: list[float], b: list[float]) -> float:
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return float(((p[0] - ax - t * dx) ** 2 + (p[1] - ay - t * dy) ** 2) ** 0.5)


def _label_box(t: dict[str, Any]) -> tuple[float, float, float, float]:
    """A label's rough extent (x0, y0, x1, y1), from its anchor, size and line count; rotation ignored."""
    rows = t["text"].split("\n")
    width = max(len(r) for r in rows) * 0.6 * t["height"]
    height = len(rows) * 1.25 * t["height"]
    x0, y_top = t["x"] - t["ax"] * width, t["y"] + t["ay"] * height
    return x0, y_top - height, x0 + width, y_top


def _box_distance(p: tuple[float, float], box: tuple[float, float, float, float]) -> float:
    dx = max(box[0] - p[0], 0.0, p[0] - box[2])
    dy = max(box[1] - p[1], 0.0, p[1] - box[3])
    return float((dx * dx + dy * dy) ** 0.5)


def depth_lines(lines: dict[str, list[list[list[float]]]], texts: list[dict[str, Any]],
                leaders: list[tuple[str, tuple[float, float], tuple[float, float]]]) -> dict[str, Any]:
    """Utility lines at the depths their labels state, joined by the drawing's own leader arrows.

    A leader whose one end is at a utility label (same layer) and whose other end is on a line of
    that layer gives the line that label's depth at that point. Along a line with several labels,
    each vertex takes the depth of its nearest label point. A line no leader reaches has no depth:
    it is returned separately, never given one.
    """
    labels = [t for t in texts if t["utility"] and t["utility"]["depth_m"] is not None]
    points: dict[tuple[str, int], list[tuple[tuple[float, float], dict[str, Any]]]] = {}
    for layer, a, b in leaders:
        mine = [t for t in labels if t["layer"] == layer]
        best = min(((_box_distance(end, _label_box(t)), end, other, t) for t in mine for end, other in ((a, b), (b, a))),
                   key=lambda item: item[0], default=None)
        if best is None or best[0] > LABEL_REACH:
            continue
        _, _, tip, label = best
        reach = [(min(_segment_distance(tip, u, v) for u, v in itertools.pairwise(line)), i)
                 for i, line in enumerate(lines.get(layer, [])) if len(line) >= 2]
        if not reach:
            continue
        gap, index = min(reach)
        if gap <= LINE_REACH:
            points.setdefault((layer, index), []).append((tip, label))
    # A utility drawn as several touching pieces is one line: the depth a leader gives one piece carries,
    # piece by piece, to the pieces joined to it end to end on the same layer.
    carried: dict[tuple[str, int], list[tuple[tuple[float, float], dict[str, Any]]]] = {}
    for layer in {layer for layer, _ in points}:
        polylines = lines.get(layer, [])
        ends = [(tuple(line[0]), tuple(line[-1])) for line in polylines]
        frontier = [i for (lay, i) in points if lay == layer]
        seen = set(frontier)
        while frontier:
            i = frontier.pop()
            source = points.get((layer, i)) or carried[(layer, i)]
            for j, (start, end) in enumerate(ends):
                if j in seen:
                    continue
                if any(((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5 <= JOIN_REACH for p in ends[i] for q in (start, end)):
                    seen.add(j)
                    carried[(layer, j)] = source
                    frontier.append(j)
    labelled_layers = {t["layer"] for t in labels}
    placed, unplaced = [], []
    for layer, polylines in lines.items():
        for i, line in enumerate(polylines):
            found = points.get((layer, i)) or carried.get((layer, i))
            if not found:
                if layer in labelled_layers:  # a utility layer whose depths are given elsewhere, not here
                    unplaced.append({"layer": layer, "points": line})
                continue
            out = []
            for x, y in line:
                _, label = min(found, key=lambda f: (f[0][0] - x) ** 2 + (f[0][1] - y) ** 2)
                out.append([x, y, label["utility"]["depth_m"]])
            placed.append({"layer": layer, "points": out, "carried": (layer, i) not in points,
                           "labels": [] if (layer, i) not in points else [{"x": tip[0], "y": tip[1], "depth_m": label["utility"]["depth_m"],
                                       "kind": label["utility"]["kind"], "quality_level": label["utility"]["quality_level"],
                                       "diameter_mm": label["utility"]["diameter_mm"]} for tip, label in found]})
    return {"placed": placed, "unplaced": unplaced, "leader_links": sum(len(v) for v in points.values()),
            "labels_with_depth": len(labels)}


def geometry(dataset_dir: Path, name: str) -> dict[str, Any]:
    """The drawing's geometry, parsed once and cached beside it until the .dxf changes."""
    dxf = path_of(dataset_dir, name)
    cache = dxf.with_name(f".{name}{_CACHE}")
    stamp = dxf.stat().st_mtime_ns
    if cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("stamp") == stamp and cached.get("format") == _FORMAT:
            return dict(cached["geometry"])
    found = {"name": name, **parse(dxf)}
    cache.write_text(json.dumps({"stamp": stamp, "format": _FORMAT, "geometry": found}), encoding="utf-8")
    return found

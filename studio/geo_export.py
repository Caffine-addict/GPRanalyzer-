"""GeoJSON → KML (Google Earth) and DXF (CAD), docs/GEOSPATIAL_PLAN.md phase 4.

KML stays in WGS 84 longitude/latitude, as the format requires. DXF is drawn in metres in the
UTM zone of the data's own centre, chosen automatically from its longitude (Bangalore lands in
43N, EPSG:32643; Gandhidham–Mundra in 42N, EPSG:32642). Data spanning more than one zone is
refused rather than drawn distorted — export one site at a time.

Every exported object keeps what the GeoJSON said about it: how its position was obtained and
its stated error travel into the KML description and the DXF text/layer names, so a CAD user
cannot mistake a model's unconfirmed claim, or a GPS-only position, for a surveyed finding.
"""

from __future__ import annotations

import io
import math
import re
from typing import Any
from xml.sax.saxutils import escape

import ezdxf
from pyproj import Proj, Transformer

# KML colours are aabbggrr.
_KML_STYLES = {
    "vendor_callout": "ff00a5ff", "radar_line": "ffff9c4f", "target": "ff41b0f5",
    "claim_proposed": "ff23a6f5", "claim_confirmed": "ff7bc734", "line_location": "80ff9c4f",
}
_DESCRIBED = ("utility", "depth_m", "depth_basis", "chainage_m", "identity", "material", "confidence", "review_status",
              "reviewer", "origin", "shape", "label", "drawing", "sheet", "position_method", "position_error_m",
              "uncertainty_radius_m", "status", "read_by")


# XML 1.0 forbids these control characters even escaped; one in a pick label or a model's claim
# would make the whole KML unreadable to strict parsers (QGIS). They carry no meaning here.
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _text(value: object) -> str:
    return escape(_XML_ILLEGAL.sub("", str(value)))


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the WGS 84 UTM zone containing a point."""
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _style(props: dict[str, Any]) -> str:
    kind = props.get("kind", "")
    if kind == "claim":
        return "claim_confirmed" if props.get("review_status") == "confirmed" else "claim_proposed"
    return kind if kind in _KML_STYLES else "target"


def _name(props: dict[str, Any]) -> str:
    kind = props.get("kind")
    if kind == "vendor_callout":
        depth = props.get("depth_m")
        return f"{props.get('utility')} {'' if depth is None else f'{depth:.2f} m'}".strip()
    if kind == "claim":
        return f"{props.get('identity')} — {props.get('review_status')}"
    if kind in ("radar_line", "line_location"):
        return str(props.get("id"))
    return f"{props.get('origin', 'target')} {props.get('chainage_m', '')} m"


def _description(props: dict[str, Any]) -> str:
    lines = [f"{key}: {props[key]}" for key in _DESCRIBED if props.get(key) not in (None, "")]
    lines += [f"warning: {w}" for w in props.get("warnings") or []]
    return "\n".join(lines)


def to_kml(collection: dict[str, Any], title: str) -> bytes:
    """A KML document; every text value escaped (names and notes come from files, people and models)."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
           f"<name>{_text(title)}</name>"]
    for style, colour in _KML_STYLES.items():
        out.append(f'<Style id="{style}"><IconStyle><color>{colour}</color><scale>0.7</scale></IconStyle>'
                   f"<LineStyle><color>{colour}</color><width>3</width></LineStyle>"
                   f"<PolyStyle><color>40{colour[2:]}</color></PolyStyle></Style>")
    for feature in collection.get("features", []):
        props, geometry = feature["properties"], feature["geometry"]
        head = (f"<Placemark><name>{_text(_name(props))}</name>"
                f"<description>{_text(_description(props))}</description><styleUrl>#{_style(props)}</styleUrl>")
        if geometry["type"] == "LineString":
            coords = " ".join(f"{lon:.7f},{lat:.7f},0" for lon, lat in geometry["coordinates"])
            out.append(f"{head}<LineString><tessellate>1</tessellate><coordinates>{coords}</coordinates></LineString></Placemark>")
        elif props.get("kind") == "line_location" and props.get("uncertainty_radius_m"):
            lon, lat = geometry["coordinates"]
            ring = _circle(lon, lat, float(props["uncertainty_radius_m"]))
            coords = " ".join(f"{x:.7f},{y:.7f},0" for x, y in ring)
            out.append(f"{head}<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates>"
                       "</LinearRing></outerBoundaryIs></Polygon></Placemark>")
        else:
            lon, lat = geometry["coordinates"]
            out.append(f"{head}<Point><coordinates>{lon:.7f},{lat:.7f},0</coordinates></Point></Placemark>")
    out.append("</Document></kml>")
    return "\n".join(out).encode("utf-8")


def _circle(lon: float, lat: float, radius_m: float, steps: int = 48) -> list[tuple[float, float]]:
    """A ring of lon/lat points `radius_m` around a point (flat-earth: fine at site scale)."""
    dlat = radius_m / 111_320
    dlon = radius_m / (111_320 * math.cos(math.radians(lat)))
    return [(lon + dlon * math.cos(2 * math.pi * i / steps), lat + dlat * math.sin(2 * math.pi * i / steps))
            for i in range(steps + 1)]


def _layer(props: dict[str, Any]) -> str:
    kind = props.get("kind")
    if kind == "vendor_callout":
        return f"VENDOR_{str(props.get('utility', 'UNKNOWN')).replace(' ', '_')}"
    if kind == "claim":
        return f"CLAIM_{str(props.get('review_status', 'proposed')).upper()}"
    if kind == "target":
        return "TARGET_PICK" if props.get("origin") == "interpreter pick" else "TARGET_CANDIDATE"
    return {"radar_line": "RADAR_LINE", "line_location": "RADAR_LINE_LOCATION_ONLY"}.get(str(kind), "OTHER")


def to_dxf(collection: dict[str, Any]) -> tuple[bytes, int]:
    """DXF in metres in the data's UTM zone. Returns (file bytes, EPSG code). ValueError if it spans zones."""
    points = [c for f in collection.get("features", [])
              for c in (f["geometry"]["coordinates"] if f["geometry"]["type"] == "LineString" else [f["geometry"]["coordinates"]])]
    if not points:
        raise ValueError("nothing to export")
    zones = {utm_epsg(lon, lat) for lon, lat in points}
    if len(zones) > 1:
        raise ValueError(f"the data spans UTM zones {sorted(zones)} — export one site at a time")
    epsg = zones.pop()
    to_utm = Transformer.from_crs(4326, epsg, always_xy=True)
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 6  # metres
    space = doc.modelspace()
    for feature in collection["features"]:
        props, geometry = feature["properties"], feature["geometry"]
        layer = _layer(props)
        if layer not in doc.layers:
            doc.layers.add(layer)
        attribs = {"layer": layer}
        if geometry["type"] == "LineString":
            space.add_lwpolyline([to_utm.transform(lon, lat) for lon, lat in geometry["coordinates"]], dxfattribs=attribs)
            continue
        x, y = to_utm.transform(*geometry["coordinates"])
        if props.get("kind") == "line_location":
            space.add_circle((x, y), float(props.get("uncertainty_radius_m") or 1.0), dxfattribs=attribs)
        else:
            space.add_circle((x, y), 0.15, dxfattribs=attribs)
        label = _name(props)
        if props.get("position_error_m") is not None:
            label += f" (±{props['position_error_m']} m)"
        space.add_text(label, height=0.25, dxfattribs={**attribs, "insert": (x + 0.25, y + 0.25)})
    # UTM is a grid: distances on it differ from ground distances by the zone's scale factor
    # (about 1.0018 in Bangalore, 2.6 degrees off the central meridian). Said on the drawing.
    lon_c = sum(lon for lon, _ in points) / len(points)
    lat_c = sum(lat for _, lat in points) / len(points)
    scale = grid_scale(lon_c, lat_c, epsg)
    x, y = to_utm.transform(lon_c, lat_c)
    if "NOTES" not in doc.layers:
        doc.layers.add("NOTES")
    space.add_text(f"WGS 84 / UTM EPSG:{epsg}, metres. Grid distance = ground distance x {scale:.5f} here. "
                   "Positions carry their stated error; CLAIM_PROPOSED objects are unconfirmed model claims.",
                   height=0.3, dxfattribs={"layer": "NOTES", "insert": (x, y - 3)})
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8"), epsg


def grid_scale(lon: float, lat: float, epsg: int) -> float:
    """UTM point scale factor at a location: grid distance / ground distance."""
    return float(Proj(f"EPSG:{epsg}").get_factors(lon, lat).meridional_scale)

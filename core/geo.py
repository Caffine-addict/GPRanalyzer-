"""Site-scale geodesy on the WGS 84 ellipsoid: distances, and a local flat frame.

Distances are true geodesics (pyproj's Geod). The local east/north frame uses the ellipsoid's
own radii of curvature at the origin, which keeps it within millimetres of the ellipsoid over a
survey road or a site. An earlier spherical version was 0.1-0.2% off at Bangalore's latitude —
a centimetre on a 10 m line, more than a metre on a kilometre road — and a DXF round-trip test
caught it.

GeoJSON order is (longitude, latitude) everywhere in this project; the helpers here take and
return (lat, lon) only where the argument names say so, to keep the two from being swapped.
"""

from __future__ import annotations

import math
from itertools import pairwise

from pyproj import Geod

_WGS84 = Geod(ellps="WGS84")
_A = 6_378_137.0  # WGS 84 semi-major axis
_E2 = 0.00669437999014  # WGS 84 first eccentricity squared


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Geodesic distance in metres on the WGS 84 ellipsoid."""
    _, _, metres = _WGS84.inv(lon1, lat1, lon2, lat2)
    return float(metres)


def _radii(lat: float) -> tuple[float, float]:
    """(meridional, prime-vertical) radii of curvature at a latitude."""
    s2 = math.sin(math.radians(lat)) ** 2
    meridional = _A * (1 - _E2) / (1 - _E2 * s2) ** 1.5
    prime_vertical = _A / math.sqrt(1 - _E2 * s2)
    return meridional, prime_vertical


def to_local(lat: float, lon: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    """(east, north) metres from the origin, on a plane tangent at the origin."""
    meridional, prime_vertical = _radii(origin_lat)
    east = math.radians(lon - origin_lon) * prime_vertical * math.cos(math.radians(origin_lat))
    north = math.radians(lat - origin_lat) * meridional
    return east, north


def from_local(east: float, north: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    """(lat, lon) of a point `east`, `north` metres from the origin."""
    meridional, prime_vertical = _radii(origin_lat)
    lat = origin_lat + math.degrees(north / meridional)
    lon = origin_lon + math.degrees(east / (prime_vertical * math.cos(math.radians(origin_lat))))
    return lat, lon


def interpolate(chainage: float, anchors: list[tuple[float, float, float]]) -> tuple[float, float] | None:
    """(lat, lon) at `chainage` along a path given as (chainage, lat, lon) anchors, or None outside it.

    Linear between the two anchors that bracket the chainage, in a local flat frame. Outside the
    first and last anchor there is nothing to interpolate from, so the answer is None rather than
    an extrapolation that would look as trustworthy as the rest.
    """
    if len(anchors) < 2:
        return None
    ordered = sorted(anchors)
    if not ordered[0][0] <= chainage <= ordered[-1][0]:
        return None
    for (c0, lat0, lon0), (c1, lat1, lon1) in pairwise(ordered):
        if c0 <= chainage <= c1:
            if c1 == c0:
                return lat0, lon0
            t = (chainage - c0) / (c1 - c0)
            east, north = to_local(lat1, lon1, lat0, lon0)
            return from_local(east * t, north * t, lat0, lon0)
    return None  # unreachable: the range check above guarantees a bracketing pair

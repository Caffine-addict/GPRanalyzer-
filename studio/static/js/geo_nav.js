/* Distance and direction between two positions, for walking a crew to a dig item.
 *
 * Local flat-earth on the WGS 84 ellipsoid's radii at the starting latitude: over the few hundred
 * metres of a site the error is millimetres, far below phone GPS (3-5 m). Checked against the
 * server's geodesic distance (core/geo.py) by tests/test_field_nav.py.
 */

const A = 6378137.0; // WGS 84 semi-major axis (m)
const E2 = 0.00669437999014; // first eccentricity squared

function radii(latDeg) {
  const s2 = Math.sin((latDeg * Math.PI) / 180) ** 2;
  const meridional = (A * (1 - E2)) / (1 - E2 * s2) ** 1.5;
  const primeVertical = A / Math.sqrt(1 - E2 * s2);
  return { meridional, primeVertical };
}

/** Metres east and north from (lat1, lon1) to (lat2, lon2). */
export function offsetM(lat1, lon1, lat2, lon2) {
  const { meridional, primeVertical } = radii((lat1 + lat2) / 2);
  const rad = Math.PI / 180;
  return {
    east: (lon2 - lon1) * rad * primeVertical * Math.cos(((lat1 + lat2) / 2) * rad),
    north: (lat2 - lat1) * rad * meridional,
  };
}

export function distanceM(lat1, lon1, lat2, lon2) {
  const { east, north } = offsetM(lat1, lon1, lat2, lon2);
  return Math.hypot(east, north);
}

/** Compass bearing in degrees (0 = north, 90 = east) from the first position to the second. */
export function bearingDeg(lat1, lon1, lat2, lon2) {
  const { east, north } = offsetM(lat1, lon1, lat2, lon2);
  return ((Math.atan2(east, north) * 180) / Math.PI + 360) % 360;
}

const POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];

export function compassPoint(bearing) {
  return POINTS[Math.round(bearing / 45) % 8];
}

export const MAX_USABLE_ACCURACY_M = 15; // a worse phone fix cannot even find the area
export const MAX_FIX_AGE_MS = 10_000; // an older fix no longer says where the crew is

/**
 * What the crew should be told about one item from where they stand.
 *
 * Phone GPS is only good for walking to the area. A fix that is too poor or too old guides nobody:
 * the answer is to wait for GPS or tape out. When the remaining distance is within the combined
 * position errors, the answer is the tape-out instruction, never an arrow. `now` is in ms.
 */
export function guidance(here, item, now = Date.now()) {
  if (!here) return { usable: false, text: "Waiting for GPS…" };
  if (here.timestamp != null && now - here.timestamp > MAX_FIX_AGE_MS) {
    return { usable: false, text: `GPS lost ${Math.round((now - here.timestamp) / 1000)} s ago — stand still in the open, or tape out: ${item.tape}.` };
  }
  if ((here.accuracy ?? Infinity) > MAX_USABLE_ACCURACY_M) {
    return { usable: false, text: `GPS too weak (±${Math.round(here.accuracy ?? 0)} m) to guide you — tape out: ${item.tape}.` };
  }
  const metres = distanceM(here.lat, here.lon, item.lat, item.lon);
  const combined = Math.hypot(here.accuracy, item.horizontal_error_m ?? 0);
  if (metres <= combined) {
    return { usable: true, metres, arrived: true, text: `You are within GPS error (±${combined.toFixed(1)} m). Mark it by tape: ${item.tape}.` };
  }
  const bearing = bearingDeg(here.lat, here.lon, item.lat, item.lon);
  return { usable: true, metres, arrived: false, bearing, text: `${metres.toFixed(1)} m ${compassPoint(bearing)} (±${combined.toFixed(1)} m)` };
}

/** Which way to point an arrow on screen: the bearing relative to where the phone faces. */
export function arrowAngle(bearing, heading) {
  return ((bearing - heading) % 360 + 360) % 360;
}

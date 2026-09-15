/* Thin wrapper over the Studio HTTP API (studio/server.py).
 *
 * One rule here: a failed request throws with the server's own detail message
 * attached. Panels surface that text verbatim rather than substituting a
 * friendly generic — when a processing parameter is rejected, the reason is
 * the useful part.
 */

async function request(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body — the status line is all there is */
    }
    throw new Error(detail);
  }
  return response.json();
}

/** Serialise the processing chain + display settings into an image query string. */
export function viewParams(processing, display, size) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(processing)) params.set(key, String(value));
  params.set("palette", display.palette);
  params.set("contrast", String(display.contrast));
  params.set("brightness", String(display.brightness));
  if (size?.width) params.set("width", String(Math.round(size.width)));
  if (size?.height) params.set("height", String(Math.round(size.height)));
  return params;
}

export const listJobs = () => request("/api/jobs");
export const getJob = (job) => request(`/api/jobs/${encodeURIComponent(job)}`);
export const listPalettes = () => request("/api/palettes");
export const listReference = () => request("/api/reference");

export function imageUrl(job, channel, processing, display, size) {
  const query = viewParams(processing, display, size);
  return `/api/jobs/${encodeURIComponent(job)}/channels/${channel}/image.png?${query}`;
}

export function getTrace(job, channel, index, processing) {
  const query = new URLSearchParams({ index: String(index) });
  for (const [key, value] of Object.entries(processing)) query.set(key, String(value));
  return request(`/api/jobs/${encodeURIComponent(job)}/channels/${channel}/trace?${query}`);
}

export function fitHyperbola(job, channel, region) {
  return request(`/api/jobs/${encodeURIComponent(job)}/channels/${channel}/fit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(region),
  });
}

export function previewCurve(job, channel, apexTrace, apexTimeNs, velocity) {
  const query = new URLSearchParams({
    apex_trace: String(apexTrace),
    apex_time_ns: String(apexTimeNs),
    velocity_m_per_ns: String(velocity),
  });
  return request(`/api/jobs/${encodeURIComponent(job)}/channels/${channel}/curve?${query}`);
}

export const listCandidates = (job) => request(`/api/jobs/${encodeURIComponent(job)}/candidates`);

export const listPicks = (job) => request(`/api/jobs/${encodeURIComponent(job)}/picks`);

export const createPick = (job, pick) =>
  request(`/api/jobs/${encodeURIComponent(job)}/picks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(pick),
  });

export const deletePick = (job, pickId) =>
  request(`/api/jobs/${encodeURIComponent(job)}/picks/${pickId}`, { method: "DELETE" });

export const interpretPick = (job, pickId) =>
  request(`/api/jobs/${encodeURIComponent(job)}/picks/${pickId}/interpret`, { method: "POST" });

export const picksCsvUrl = (job) => `/api/jobs/${encodeURIComponent(job)}/picks.csv`;

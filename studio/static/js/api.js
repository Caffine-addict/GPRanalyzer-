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

/* ---------- importing files (studio/imports.py) ---------- */

function formWith(files, fields = {}) {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) form.append(key, value);
  for (const file of files) form.append("files", file, file.name);
  return form;
}

export const importLine = (job, files) =>
  request("/api/import/line", { method: "POST", body: formWith(files, { job }) });
export const importReferences = (files) =>
  request("/api/import/references", { method: "POST", body: formWith(files) });
export const listImportedReferences = () => request("/api/import/references");
export const importedReferenceUrl = (name) => `/api/import/references/${encodeURIComponent(name)}`;
export const listDocuments = () => request("/api/documents");
export const documentUrl = (path) => `/api/documents/${path.split("/").map(encodeURIComponent).join("/")}`;

/* ---------- reasoning layers and supervisor review (studio/assistant_routes.py) ---------- */

export const askAssistant = (job, channel, body) =>
  request(`/api/jobs/${encodeURIComponent(job)}/channels/${channel}/assistant`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const surveyBriefing = () => request("/api/assistant/briefing", { method: "POST" });

export const listReviews = (job) => request(`/api/jobs/${encodeURIComponent(job)}/reviews`);

export const decideReview = (job, reviewId, decision) =>
  request(`/api/jobs/${encodeURIComponent(job)}/reviews/${reviewId}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(decision),
  });

export const reviewsCsvUrl = (job) => `/api/jobs/${encodeURIComponent(job)}/reviews.csv`;
export const reviewReportUrl = (job, channel) =>
  `/api/jobs/${encodeURIComponent(job)}/channels/${channel}/review_report.pdf`;
export const briefingReportUrl = () => "/api/assistant/briefing.pdf";

/* ---------- map and GeoJSON (studio/geo_routes.py) ---------- */

export const vendorGeojson = () => request("/api/geo/vendor.geojson");
export const georeferencedLines = () => request("/api/geo/lines");
export const lineGeojson = (job) => request(`/api/jobs/${encodeURIComponent(job)}/geo.geojson`);
export const getGeoref = (job) => request(`/api/jobs/${encodeURIComponent(job)}/georef`);
export const saveGeoref = (job, referencePoints) =>
  request(`/api/jobs/${encodeURIComponent(job)}/georef`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reference_points: referencePoints }),
  });
export const lineGeojsonUrl = (job) => `/api/jobs/${encodeURIComponent(job)}/geo.geojson?download=true`;
export const vendorGeojsonUrl = () => "/api/geo/vendor.geojson?download=true";
export const allGeojsonUrl = () => "/api/geo/all.geojson";
export const exportUrl = (format, scope) => `/api/geo/export.${format}?scope=${encodeURIComponent(scope)}`;

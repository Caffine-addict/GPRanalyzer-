/* Thin wrapper over the Studio HTTP API (studio/server.py).
 *
 * One rule here: a failed request throws with the server's own detail message
 * attached. Panels surface that text verbatim rather than substituting a
 * friendly generic — when a processing parameter is rejected, the reason is
 * the useful part.
 */

async function request(url, options, read = (response) => response.json()) {
  const response = await fetch(url, options);
  if (response.status === 401) {
    // the session ended (expired, signed out elsewhere, password changed): sign in, then come back
    window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
    throw new Error("signed out");
  }
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
  return read(response);
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
export const whoAmI = () => request("/api/me");
export const getJob = (job) => request(`/api/jobs/${encodeURIComponent(job)}`);
/** Take a line out of the project; the server keeps it in removed_lines and needs its name repeated. */
export const removeLine = (job) => request(`/api/jobs/${encodeURIComponent(job)}`, {
  method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm: job }),
});
export const removedLines = () => request("/api/removed-lines");
export const restoreLine = (id) => request(`/api/removed-lines/${encodeURIComponent(id)}/restore`, { method: "POST" });
export const listPalettes = () => request("/api/palettes");
export const listReference = () => request("/api/reference");

export function imageUrl(job, channel, processing, display, size) {
  const query = viewParams(processing, display, size);
  return `/api/jobs/${encodeURIComponent(job)}/channels/${channel}/image.png?${query}`;
}

/**
 * A few metres of radargram either side of a position along a line, with that position ticked.
 * `view` is Interpret's current processing and display, so the preview looks like the line will
 * when it is opened there.
 */
export function peekUrl(job, channel, chainageM, view, halfSpanM = 1.5) {
  const query = viewParams(view.processing, view.display, { height: 180 });
  query.set("from_m", Math.max(0, chainageM - halfSpanM).toFixed(3));
  query.set("to_m", (chainageM + halfSpanM).toFixed(3));
  query.set("mark_m", chainageM.toFixed(3));
  return `/api/jobs/${encodeURIComponent(job)}/channels/${encodeURIComponent(channel)}/image.png?${query}`;
}

/** The whole processed line, one Float32Array per fetch: trace t is samples [t*nSamples, (t+1)*nSamples). */
export function lineSamples(job, channel, processing) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(processing)) query.set(key, String(value));
  return request(`/api/jobs/${encodeURIComponent(job)}/channels/${encodeURIComponent(channel)}/samples.f32?${query}`,
    undefined, async (response) => ({
      nTraces: Number(response.headers.get("X-Traces")),
      nSamples: Number(response.headers.get("X-Samples")),
      data: new Float32Array(await response.arrayBuffer()),
    }));
}

export function fitHyperbola(job, channel, region) {
  return request(`/api/jobs/${encodeURIComponent(job)}/channels/${channel}/fit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(region),
  });
}

export function previewCurve(job, channel, apexTrace, apexTimeNs, velocity, timeZeroSample = 0) {
  const query = new URLSearchParams({
    apex_trace: String(apexTrace),
    apex_time_ns: String(apexTimeNs),
    velocity_m_per_ns: String(velocity),
    time_zero_sample: String(timeZeroSample),
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

export const listDrawings = () => request("/api/drawings");
export const drawingGeometry = (name) => request(`/api/drawings/${encodeURIComponent(name)}/geometry.json`);
export const importDrawings = (files) => request("/api/drawings", { method: "POST", body: formWith(files) });

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

export const siteReport = (site) => request(`/api/sites/${encodeURIComponent(site)}/report`);
export const saveReportForm = (site, form) => request(`/api/sites/${encodeURIComponent(site)}/report/form`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ form }),
});
export const approveReport = (site, competence, contentHash) => request(`/api/sites/${encodeURIComponent(site)}/report/approve`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ competence, content_hash: contentHash }),
});
export const surveyReportUrl = (site) => `/api/sites/${encodeURIComponent(site)}/survey_report.pdf`;
export const siteGround = (site) => request(`/api/sites/${encodeURIComponent(site)}/ground.json`);
export const siteRuns = (site) => request(`/api/sites/${encodeURIComponent(site)}/runs.json`);
export const decideRun = (site, runId, decision) =>
  request(`/api/sites/${encodeURIComponent(site)}/runs/${encodeURIComponent(runId)}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(decision),
  });
export const runsGeojsonUrl = (site) => `/api/sites/${encodeURIComponent(site)}/runs.geojson?download=true`;

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

/* ---------- field mode: a whole site on the map, the dig map, the crew's reports ---------- */
const site = (name) => `/api/sites/${encodeURIComponent(name)}`;
export const listSites = () => request("/api/sites");
export const siteScene = (site, channel, detected = true) =>
  request(`/api/sites/${encodeURIComponent(site)}/scene.json?${new URLSearchParams({ channel, detected })}`);

/** One line's processed radargram between two chainages, at native size: a 3D curtain's texture. */
export function curtainUrl(job, channel, fromM, toM, processing, display) {
  const query = viewParams(processing, display);
  query.set("from_m", fromM.toFixed(3));
  query.set("to_m", toM.toFixed(3));
  return `/api/jobs/${encodeURIComponent(job)}/channels/${encodeURIComponent(channel)}/image.png?${query}`;
}
export const siteGeojson = (name) => request(`${site(name)}/field.geojson`);
export const digGeojson = (name, includeInterpreted = false) =>
  request(`${site(name)}/dig.geojson?include_interpreted=${includeInterpreted}`);
export const siteDownloadUrl = (name) => `${site(name)}/field.geojson?download=true`;
export const digDownloadUrl = (name, format) => `${site(name)}/dig.${format}${format === "geojson" ? "?download=true" : ""}`;
export const fieldViewUrl = (name) => `/field?site=${encodeURIComponent(name)}`;
export const reportFromField = (name, report) => request(`${site(name)}/field_reports`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(report),
});
export const siteFieldReports = (name) => request(`${site(name)}/field_reports`);
export const confirmSuggestion = (name, body) => request(`${site(name)}/confirm`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

/* ---------- verifying targets for training (studio/verify.py) ---------- */

export const verifySites = () => request("/api/verify/sites");
export const verifyQueue = (site) => request(`/api/verify/queue?site=${encodeURIComponent(site)}`);
export const verifyProgress = () => request("/api/verify/progress");
export const verifyLabel = (body) => request("/api/verify/labels", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

/** One channel around a target: 1.5 m either side, ticks at its position (edges top/bottom) and time (edges left/right). */
export function verifyImageUrl(job, channel, chainageM, markNs, lineLengthM) {
  const from = Math.max(0, chainageM - 1.5);
  const to = Math.min(lineLengthM ?? Infinity, chainageM + 1.5);
  // the width follows the stretch actually shown, so a target near a line's end is not stretched sideways
  const query = new URLSearchParams({ background_removal: "mean", gain: "agc", palette: "grey", contrast: "98",
    height: "360", width: String(Math.max(40, Math.round((360 * (to - from)) / 3))), from_m: from.toFixed(3), to_m: to.toFixed(3),
    mark_m: chainageM.toFixed(3), mark_ns: markNs.toFixed(3) });
  return `/api/jobs/${encodeURIComponent(job)}/channels/${encodeURIComponent(channel)}/image.png?${query}`;
}


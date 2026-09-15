/* Single application store.
 *
 * Every update replaces state rather than mutating it, so a render pass can
 * compare `prev` against `next` by reference and skip work — and, more to the
 * point, so no panel can quietly change what another panel is showing. Same
 * immutability rule the Python side holds to in studio/processing.py.
 */

const listeners = new Set();

/* Mirrors ProcessingChain in studio/processing.py. Kept in the same order the
 * chain actually runs, so reading this is reading the pipeline. Defaults are
 * all "off": a line opens showing raw data, and every deviation from raw is
 * something the operator switched on deliberately. */
export const DEFAULT_PROCESSING = Object.freeze({
  time_zero_sample: 0,
  dewow: false,
  dewow_window_samples: 25,
  background_removal: "none",
  background_window_traces: 51,
  bandpass: false,
  bandpass_low_mhz: 100,
  bandpass_high_mhz: 1200,
  migrate: false,
  migration_velocity_m_per_ns: 0.0999,
  migration_aperture_traces: 40,
  gain: "none",
  agc_window_samples: 40,
  gain_exponent: 2,
  stack_traces: 1,
});

export const DEFAULT_DISPLAY = Object.freeze({
  palette: "grey",
  contrast: 98,
  brightness: 0,
});

let state = Object.freeze({
  jobs: [],
  job: null,
  jobDetail: null,
  channel: null,
  expandedJob: null,

  tool: "pan",
  processing: { ...DEFAULT_PROCESSING },
  display: { ...DEFAULT_DISPLAY },

  /* Viewport in data space: which trace/sample sits at the top-left of the
   * plot, and how many pixels one trace / one sample occupies. */
  view: { originTrace: 0, originSample: 0, scaleX: 1, scaleY: 1 },
  /* True while the viewport is still the automatic whole-line fit. Panel
   * layout settles after the first paint, so the canvas grows underneath a
   * fit computed too early; re-fitting on resize corrects that, but must not
   * throw away a zoom the operator set deliberately. */
  viewFitted: true,

  cursor: null,        // {trace, sample} under the pointer, data coordinates
  drag: null,          // in-progress gesture, tool-specific
  ascan: null,         // {trace, distance_m, sample_interval_ns, samples[]}

  picks: [],
  selectedPickId: null,  // the target the Interpretation panel is reading
  interpretation: null,  // last /interpret response, keyed to its pick by pick_id
  interpreting: false,
  interpretError: null,
  candidates: [],      // Phase-1 auto-detected regions, read-only (studio/candidates.py)
  showCandidates: false,
  fit: null,           // last automatic fit result from the server
  hyperbola: null,     // {apexTrace, apexTimeNs, velocity, curve[], source}
  measure: null,       // {from:{trace,sample}, to:{trace,sample}}

  palettes: [],
  reference: [],
  status: null,        // transient message for the chain pill
});

export function getState() {
  return state;
}

export function setState(patch) {
  state = Object.freeze({ ...state, ...patch });
  for (const listener of listeners) listener(state);
  return state;
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The channel metadata for whatever channel is open, or null. */
export function activeChannel(s = state) {
  if (!s.jobDetail || !s.channel) return null;
  return s.jobDetail.channels.find((c) => c.extension === s.channel) ?? null;
}

/** Velocity currently driving every depth on screen, and where it came from.
 *
 * Precedence is deliberate: a velocity measured off a hyperbola in *this* line
 * beats the header's operator-typed dielectric, because one is a measurement
 * of this ground and the other is a site setting. The source travels with the
 * number everywhere it is displayed — a depth whose provenance is invisible is
 * the failure mode this whole project is built to avoid. */
export function activeVelocity(s = state) {
  if (s.hyperbola) {
    return { value: s.hyperbola.velocity, source: s.hyperbola.source };
  }
  const channel = activeChannel(s);
  if (channel?.dielectric_assumed) {
    return { value: 0.2998 / Math.sqrt(channel.dielectric_assumed), source: "assumed" };
  }
  return { value: null, source: "unavailable" };
}

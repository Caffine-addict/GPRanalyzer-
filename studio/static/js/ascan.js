/* The A-scan strip: the single vertical trace under the cursor, drawn as a wiggle.
 *
 * A radargram is a picture of many traces side by side; the A-scan is one of
 * them on its own. Interpreters read it constantly, because polarity and
 * wavelet shape — which reflection is positive-first, how many cycles ring
 * after it — are visible in a waveform and essentially invisible in a
 * greyscale column. That is also how you tell a pipe from a void.
 *
 * Samples come from the server already run through the processing chain, so
 * the wiggle always shows the same signal as the picture above it.
 */

const canvas = document.getElementById("ascan");
const ctx = canvas.getContext("2d");
const label = document.getElementById("ascanLabel");

export function resize() {
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(canvas.clientWidth * ratio);
  canvas.height = Math.round(canvas.clientHeight * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
}

export function draw(ascan, highlightSample) {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const ratio = window.devicePixelRatio || 1;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);

  if (!ascan?.samples?.length) {
    label.textContent = "—";
    ctx.fillStyle = "#5c6673";
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("move the cursor over the radargram", width / 2, height / 2);
    return;
  }

  const samples = ascan.samples;
  // Symmetric about zero: an A-scan's whole point is polarity, and a min-max
  // scale would shift the zero line off centre and make a mostly-positive
  // trace look bipolar.
  const peak = Math.max(...samples.map(Math.abs)) || 1;
  const midY = height / 2;
  const stepX = width / (samples.length - 1);

  ctx.strokeStyle = "#212832";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, midY + 0.5);
  ctx.lineTo(width, midY + 0.5);
  ctx.stroke();

  ctx.strokeStyle = "#4ea3d8";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  samples.forEach((value, index) => {
    const x = index * stepX;
    const y = midY - (value / peak) * (height / 2 - 4);
    index === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();

  if (highlightSample != null && highlightSample >= 0 && highlightSample < samples.length) {
    const x = highlightSample * stepX;
    ctx.strokeStyle = "#f0a830";
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  label.textContent =
    `trace ${ascan.trace} · ${ascan.distance_m.toFixed(3)} m · ` +
    `${samples.length} samples · ${(samples.length * ascan.sample_interval_ns).toFixed(1)} ns window`;
}

import type { ConfidenceLevel } from "../api/types";

// Never render a bare number that implies a real measurement when the
// backend has explicitly labelled it "estimated" or "unavailable" — the
// same discipline core/contracts.py enforces server-side (Evidence.__post_init__:
// a value must be None iff its confidence is "unavailable") carries through
// to how it's displayed.
export function ConfidenceValue({
  value,
  confidence,
  unit = "",
}: {
  value: number | null;
  confidence: ConfidenceLevel;
  unit?: string;
}) {
  if (confidence === "unavailable" || value === null) {
    return <span style={{ fontStyle: "italic", color: "#888" }}>unavailable</span>;
  }
  return (
    <span>
      {value.toFixed(3)}
      {unit}{" "}
      <span style={{ fontSize: "0.75rem", color: confidence === "calibrated" ? "#2e7d32" : "#888" }}>
        ({confidence})
      </span>
    </span>
  );
}

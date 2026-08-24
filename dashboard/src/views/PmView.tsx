import { useState } from "react";
import { ApiError, getLineFindings } from "../api/client";
import type { Finding, RiskLevel } from "../api/types";
import { FindingTable } from "../components/FindingTable";

// The PM's job: how has this physical line trended across every survey
// that's ever covered it — not one run, but the whole history at a
// location. get_findings_for_line_id (store/base.py) is exactly this: scoped
// by line_id, spanning every survey_id, not one run.
export function PmView() {
  const [lineId, setLineId] = useState("line_1");
  const [findings, setFindings] = useState<Finding[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);

  async function handleSearch() {
    setLoading(true);
    setError(null);
    setSearched(true);
    try {
      setFindings(await getLineFindings(lineId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load line history.");
    } finally {
      setLoading(false);
    }
  }

  const byRiskLevel = findings.reduce<Record<RiskLevel, number>>(
    (acc, f) => {
      acc[f.risk_level] += 1;
      return acc;
    },
    { LOW: 0, MEDIUM: 0, HIGH: 0 },
  );

  return (
    <div style={{ padding: "1rem" }}>
      <h1>PM</h1>
      <p style={{ color: "#666" }}>Cross-survey history for one physical line.</p>

      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "1rem" }}>
        <label>
          Line ID <input value={lineId} onChange={(e) => setLineId(e.target.value)} />
        </label>
        <button type="button" onClick={handleSearch} disabled={loading}>
          Search
        </button>
      </div>

      {error && <p style={{ color: "#c62828" }}>{error}</p>}
      {loading && <p>Loading…</p>}

      {searched && !loading && !error && (
        <>
          <p>
            {findings.length} findings across every survey — LOW={byRiskLevel.LOW}, MEDIUM=
            {byRiskLevel.MEDIUM}, HIGH={byRiskLevel.HIGH}
          </p>
          <FindingTable findings={findings} />
        </>
      )}
    </div>
  );
}

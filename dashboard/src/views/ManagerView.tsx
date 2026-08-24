import { useEffect, useState } from "react";
import { ApiError, getSurveyFindings, getSurveySummary, listSurveys } from "../api/client";
import type { Finding, SurveyRecord, SurveySummary } from "../api/types";
import { FindingTable } from "../components/FindingTable";

// The manager's job: see every survey (running or finished) and drill into
// one to review its findings and risk breakdown — a retrospective/current-
// state view over REST, not a live stream.
export function ManagerView() {
  const [surveys, setSurveys] = useState<SurveyRecord[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [summary, setSummary] = useState<SurveySummary | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function refreshSurveys() {
    setError(null);
    try {
      setSurveys(await listSurveys());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load surveys.");
    }
  }

  useEffect(() => {
    refreshSurveys();
  }, []);

  async function selectSurvey(surveyId: string) {
    setSelected(surveyId);
    setLoading(true);
    setError(null);
    try {
      const [s, f] = await Promise.all([getSurveySummary(surveyId), getSurveyFindings(surveyId)]);
      setSummary(s);
      setFindings(f);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load survey detail.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ padding: "1rem" }}>
      <h1>Manager</h1>
      <button type="button" onClick={refreshSurveys}>
        Refresh
      </button>
      {error && <p style={{ color: "#c62828" }}>{error}</p>}

      <table style={{ marginTop: "1rem" }}>
        <thead>
          <tr>
            <th>Survey</th>
            <th>Line</th>
            <th>Status</th>
            <th>Source</th>
            <th>Started</th>
            <th>Stopped</th>
          </tr>
        </thead>
        <tbody>
          {surveys.map((s) => (
            <tr
              key={s.survey_id}
              onClick={() => selectSurvey(s.survey_id)}
              style={{ cursor: "pointer", fontWeight: selected === s.survey_id ? 700 : 400 }}
            >
              <td>{s.survey_id}</td>
              <td>{s.line_id}</td>
              <td>{s.status}</td>
              <td>{s.source_type}</td>
              <td>{s.started_at}</td>
              <td>{s.stopped_at ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {surveys.length === 0 && <p style={{ color: "#888" }}>No surveys yet.</p>}

      {selected && (
        <div style={{ marginTop: "1.5rem" }}>
          <h2>{selected}</h2>
          {loading && <p>Loading…</p>}
          {summary && (
            <p>
              {summary.total_findings} findings — by risk:{" "}
              {Object.entries(summary.by_risk_level)
                .map(([level, count]) => `${level}=${count}`)
                .join(", ") || "none"}
              ; by class:{" "}
              {Object.entries(summary.by_class)
                .map(([cls, count]) => `${cls}=${count}`)
                .join(", ") || "none"}
            </p>
          )}
          <FindingTable findings={findings} />
        </div>
      )}
    </div>
  );
}

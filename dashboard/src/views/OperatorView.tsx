import { useState } from "react";
import { ApiError, startSurvey, stopSurvey } from "../api/client";
import { useLiveFindings } from "../api/useLiveFindings";
import type { SurveyRecord } from "../api/types";
import { RiskBadge } from "../components/RiskBadge";

// The operator's job: start a survey, watch findings arrive in real time,
// stop it when the line is done. Retrospective analysis (past surveys,
// cross-line trends) belongs to Manager/PM — this view only ever looks at
// "right now."
export function OperatorView() {
  const [surveyId, setSurveyId] = useState("survey-1");
  const [lineId, setLineId] = useState("line_1");
  const [record, setRecord] = useState<SurveyRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isRunning = record?.status === "running";
  const { events, connectionState, clear } = useLiveFindings(isRunning);

  async function handleStart() {
    setBusy(true);
    setError(null);
    try {
      const started = await startSurvey(surveyId, lineId);
      setRecord(started);
      clear();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to start survey.");
    } finally {
      setBusy(false);
    }
  }

  async function handleStop() {
    setBusy(true);
    setError(null);
    try {
      const stopped = await stopSurvey(surveyId);
      setRecord(stopped);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to stop survey.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ padding: "1rem" }}>
      <h1>Operator</h1>

      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "1rem" }}>
        <label>
          Survey ID{" "}
          <input value={surveyId} onChange={(e) => setSurveyId(e.target.value)} disabled={isRunning} />
        </label>
        <label>
          Line ID{" "}
          <input value={lineId} onChange={(e) => setLineId(e.target.value)} disabled={isRunning} />
        </label>
        <button type="button" onClick={handleStart} disabled={busy || isRunning}>
          Start
        </button>
        <button type="button" onClick={handleStop} disabled={busy || !isRunning}>
          Stop
        </button>
      </div>

      {error && <p style={{ color: "#c62828" }}>{error}</p>}

      {record && (
        <div style={{ marginBottom: "1rem" }}>
          <p>
            Status: <strong>{record.status}</strong>
            {isRunning && (
              <span style={{ marginLeft: "0.75rem", color: connectionState === "open" ? "#2e7d32" : "#c62828" }}>
                live feed: {connectionState}
              </span>
            )}
          </p>
          <p style={{ fontSize: "0.85rem", color: "#666" }}>
            capabilities — depth: {record.capabilities.has_calibrated_depth ? "calibrated" : "uncalibrated"},
            position: {record.capabilities.has_real_position ? "real" : "synthetic"}, amplitude:{" "}
            {record.capabilities.has_true_amplitude ? "true" : "unavailable"}, latency:{" "}
            {record.capabilities.latency_class}
          </p>
        </div>
      )}

      <h2>Live feed</h2>
      {events.length === 0 ? (
        <p style={{ color: "#888" }}>No events yet.</p>
      ) : (
        <ul style={{ listStyle: "none", padding: 0 }}>
          {events
            .slice()
            .reverse()
            .map((event, index) => (
              // Append-only event log, newest first — index is stable
              // because entries are never reordered or removed, only
              // prepended-to-view via reverse().
              <li
                key={events.length - index}
                style={{ padding: "0.4rem 0", borderBottom: "1px solid #eee" }}
              >
                <span
                  style={{
                    fontWeight: 600,
                    color: event.type === "finding.created" ? "#1565c0" : "#2e7d32",
                  }}
                >
                  {event.type === "finding.created" ? "created" : "reasoned"}
                </span>{" "}
                {event.finding.evidence.detection_class} <RiskBadge level={event.finding.risk_level} />
                {event.type === "finding.reasoned" && event.finding.what && (
                  <span style={{ marginLeft: "0.5rem" }}>
                    — {event.finding.what}. {event.finding.recommended_action}
                  </span>
                )}
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}

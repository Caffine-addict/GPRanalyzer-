// Mirrors api/schemas.py and core/contracts.py exactly — field names and
// literal unions must stay in sync with the Python side by hand, since
// there's no shared schema generation between the two languages.

export type ConfidenceLevel = "calibrated" | "estimated" | "unavailable";
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH";
export type LatencyClass = "realtime" | "near_realtime" | "batch";
export type SurveyStatus = "running" | "completed" | "stopped" | "failed";

export interface Evidence {
  detection_class: string;
  detection_confidence: number;
  depth_m: number | null;
  depth_confidence: ConfidenceLevel;
  position_m: number | null;
  position_confidence: ConfidenceLevel;
  amplitude: number | null;
  amplitude_confidence: ConfidenceLevel;
  hyperbola_width_px: number;
  neighbours: string[];
}

export interface Finding {
  evidence: Evidence;
  risk_level: RiskLevel;
  risk_score: number;
  risk_rules_fired: string[];
  what: string | null;
  where: string | null;
  why: string | null;
  how: string | null;
  recommended_action: string | null;
  reasoning_latency_ms: number | null;
}

export interface SourceCapabilities {
  has_calibrated_depth: boolean;
  has_real_position: boolean;
  has_true_amplitude: boolean;
  latency_class: LatencyClass;
}

export interface SurveyRecord {
  survey_id: string;
  line_id: string;
  status: SurveyStatus;
  source_type: string;
  capabilities: SourceCapabilities;
  started_at: string;
  stopped_at: string | null;
}

export interface SurveySummary {
  survey_id: string;
  total_findings: number;
  by_risk_level: Record<string, number>;
  by_class: Record<string, number>;
}

export type FindingEventType = "finding.created" | "finding.reasoned";

// No finding_id: Finding is a pure domain object (core/contracts.py) and the
// backend deliberately doesn't attach a storage-layer ID to the broadcast
// payload. Two events for "the same" finding (created, then reasoned) can't
// be reliably correlated client-side — the live feed treats every event as
// its own immutable log entry rather than pretending to merge them.
export interface FindingEvent {
  type: FindingEventType;
  survey_id: string;
  finding: Finding;
}

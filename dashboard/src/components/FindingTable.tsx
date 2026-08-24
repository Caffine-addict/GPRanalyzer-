import type { Finding } from "../api/types";
import { ConfidenceValue } from "./ConfidenceValue";
import { RiskBadge } from "./RiskBadge";

// Shared between ManagerView and PmView — both render a static list of
// findings fetched over REST (as opposed to OperatorView's live event log,
// which has different semantics: an append-only stream, not a snapshot).
export function FindingTable({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) {
    return <p>No findings.</p>;
  }

  return (
    <table>
      <thead>
        <tr>
          <th>Class</th>
          <th>Risk</th>
          <th>Depth</th>
          <th>Position</th>
          <th>Amplitude</th>
          <th>What</th>
          <th>Recommended action</th>
        </tr>
      </thead>
      <tbody>
        {findings.map((finding, index) => (
          // Findings are an immutable, append-only audit trail (see
          // core/contracts.py's Finding docstring) with no client-visible
          // ID — index is stable here because this list is a snapshot,
          // never reordered or spliced after render.
          <tr key={index}>
            <td>{finding.evidence.detection_class}</td>
            <td>
              <RiskBadge level={finding.risk_level} />
            </td>
            <td>
              <ConfidenceValue
                value={finding.evidence.depth_m}
                confidence={finding.evidence.depth_confidence}
                unit="m"
              />
            </td>
            <td>
              <ConfidenceValue
                value={finding.evidence.position_m}
                confidence={finding.evidence.position_confidence}
                unit="m"
              />
            </td>
            <td>
              <ConfidenceValue
                value={finding.evidence.amplitude}
                confidence={finding.evidence.amplitude_confidence}
              />
            </td>
            <td>{finding.what ?? <span style={{ color: "#888" }}>pending reasoning</span>}</td>
            <td>{finding.recommended_action ?? <span style={{ color: "#888" }}>—</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

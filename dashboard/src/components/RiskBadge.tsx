import type { RiskLevel } from "../api/types";

const COLORS: Record<RiskLevel, string> = {
  LOW: "#2e7d32",
  MEDIUM: "#e08e00",
  HIGH: "#c62828",
};

export function RiskBadge({ level }: { level: RiskLevel }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "0.15rem 0.5rem",
        borderRadius: "4px",
        fontSize: "0.75rem",
        fontWeight: 600,
        color: "white",
        backgroundColor: COLORS[level],
      }}
    >
      {level}
    </span>
  );
}

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FindingTable } from "./FindingTable";
import type { Finding } from "../api/types";

function makeFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    evidence: {
      detection_class: "cavities",
      detection_confidence: 0.8,
      depth_m: 0.5,
      depth_confidence: "calibrated",
      position_m: null,
      position_confidence: "unavailable",
      amplitude: null,
      amplitude_confidence: "unavailable",
      hyperbola_width_px: 12,
      neighbours: [],
    },
    risk_level: "HIGH",
    risk_score: 0.75,
    risk_rules_fired: [],
    what: null,
    where: null,
    why: null,
    how: null,
    recommended_action: null,
    reasoning_latency_ms: null,
    ...overrides,
  };
}

describe("FindingTable", () => {
  it("shows a placeholder message when there are no findings", () => {
    render(<FindingTable findings={[]} />);
    expect(screen.getByText("No findings.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("renders one row per finding with its detection class and risk", () => {
    render(<FindingTable findings={[makeFinding({ evidence: { ...makeFinding().evidence, detection_class: "cavities" } }), makeFinding({ evidence: { ...makeFinding().evidence, detection_class: "disturbed_zone" } })]} />);

    expect(screen.getByText("cavities")).toBeInTheDocument();
    expect(screen.getByText("disturbed_zone")).toBeInTheDocument();
    expect(screen.getAllByText("HIGH")).toHaveLength(2);
  });

  it("shows 'pending reasoning' when a finding has no reasoning yet", () => {
    render(<FindingTable findings={[makeFinding({ what: null })]} />);
    expect(screen.getByText("pending reasoning")).toBeInTheDocument();
  });

  it("shows the reasoning text once it has landed", () => {
    render(
      <FindingTable
        findings={[makeFinding({ what: "a linear reflector", recommended_action: "confirm with a second pass" })]}
      />,
    );
    expect(screen.getByText("a linear reflector")).toBeInTheDocument();
    expect(screen.getByText("confirm with a second pass")).toBeInTheDocument();
  });
});

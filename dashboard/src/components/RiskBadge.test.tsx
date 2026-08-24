import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RiskBadge } from "./RiskBadge";

describe("RiskBadge", () => {
  it.each(["LOW", "MEDIUM", "HIGH"] as const)("renders the %s label", (level) => {
    render(<RiskBadge level={level} />);
    expect(screen.getByText(level)).toBeInTheDocument();
  });

  it("uses a distinct color per level", () => {
    const { rerender } = render(<RiskBadge level="LOW" />);
    const lowColor = screen.getByText("LOW").style.backgroundColor;

    rerender(<RiskBadge level="HIGH" />);
    const highColor = screen.getByText("HIGH").style.backgroundColor;

    expect(lowColor).not.toBe(highColor);
  });
});

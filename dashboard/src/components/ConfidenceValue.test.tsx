import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConfidenceValue } from "./ConfidenceValue";

describe("ConfidenceValue", () => {
  it("renders 'unavailable' and never a number when confidence is unavailable", () => {
    render(<ConfidenceValue value={null} confidence="unavailable" />);
    expect(screen.getByText("unavailable")).toBeInTheDocument();
  });

  it("renders 'unavailable' even if a value were somehow present alongside 'unavailable'", () => {
    // Defense in depth: the backend's own Evidence.__post_init__ forbids
    // this combination, but the UI must never surface a fabricated-looking
    // number regardless of what a malformed payload contains.
    render(<ConfidenceValue value={5} confidence="unavailable" />);
    expect(screen.getByText("unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/5\.000/)).not.toBeInTheDocument();
  });

  it("renders the value and confidence label when calibrated", () => {
    render(<ConfidenceValue value={0.523} confidence="calibrated" unit="m" />);
    expect(screen.getByText(/0\.523m/)).toBeInTheDocument();
    expect(screen.getByText("(calibrated)")).toBeInTheDocument();
  });

  it("renders the value and confidence label when estimated", () => {
    render(<ConfidenceValue value={1.2} confidence="estimated" />);
    expect(screen.getByText("(estimated)")).toBeInTheDocument();
  });
});

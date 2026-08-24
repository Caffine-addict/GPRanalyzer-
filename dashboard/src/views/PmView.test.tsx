import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PmView } from "./PmView";
import { ApiError } from "../api/client";
import type { Finding } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getLineFindings: vi.fn() };
});

import { getLineFindings } from "../api/client";

function finding(risk_level: Finding["risk_level"]): Finding {
  return {
    evidence: {
      detection_class: "cavities",
      detection_confidence: 0.8,
      depth_m: null,
      depth_confidence: "unavailable",
      position_m: null,
      position_confidence: "unavailable",
      amplitude: null,
      amplitude_confidence: "unavailable",
      hyperbola_width_px: 10,
      neighbours: [],
    },
    risk_level,
    risk_score: 0.5,
    risk_rules_fired: [],
    what: null,
    where: null,
    why: null,
    how: null,
    recommended_action: null,
    reasoning_latency_ms: null,
  };
}

describe("PmView", () => {
  it("shows nothing until a search has been run", () => {
    render(<PmView />);
    expect(screen.queryByText(/findings across every survey/)).not.toBeInTheDocument();
  });

  it("searches by line_id and shows a per-risk-level breakdown", async () => {
    vi.mocked(getLineFindings).mockResolvedValueOnce([finding("HIGH"), finding("HIGH"), finding("LOW")]);
    const user = userEvent.setup();
    render(<PmView />);

    await user.clear(screen.getByLabelText("Line ID"));
    await user.type(screen.getByLabelText("Line ID"), "line_42");
    await user.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => expect(screen.getByText(/3 findings across every survey/)).toBeInTheDocument());
    expect(screen.getByText(/LOW=1/)).toBeInTheDocument();
    expect(screen.getByText(/HIGH=2/)).toBeInTheDocument();
    expect(getLineFindings).toHaveBeenCalledWith("line_42");
  });

  it("shows an error message when the search fails", async () => {
    vi.mocked(getLineFindings).mockRejectedValueOnce(new ApiError(500, "internal error"));
    const user = userEvent.setup();
    render(<PmView />);

    await user.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => expect(screen.getByText("internal error")).toBeInTheDocument());
  });

  it("shows a zero-findings breakdown rather than hiding the summary when a line has no history", async () => {
    vi.mocked(getLineFindings).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    render(<PmView />);

    await user.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => expect(screen.getByText(/0 findings across every survey/)).toBeInTheDocument());
  });
});

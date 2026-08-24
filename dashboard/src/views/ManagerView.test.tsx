import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ManagerView } from "./ManagerView";
import { ApiError } from "../api/client";
import type { Finding, SurveyRecord, SurveySummary } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    listSurveys: vi.fn(),
    getSurveySummary: vi.fn(),
    getSurveyFindings: vi.fn(),
  };
});

import { getSurveyFindings, getSurveySummary, listSurveys } from "../api/client";

const survey: SurveyRecord = {
  survey_id: "survey-1",
  line_id: "line_1",
  status: "completed",
  source_type: "replay",
  capabilities: {
    has_calibrated_depth: false,
    has_real_position: false,
    has_true_amplitude: false,
    latency_class: "batch",
  },
  started_at: "2026-01-01T00:00:00Z",
  stopped_at: "2026-01-01T00:05:00Z",
};

const summary: SurveySummary = {
  survey_id: "survey-1",
  total_findings: 2,
  by_risk_level: { HIGH: 2 },
  by_class: { cavities: 2 },
};

const findings: Finding[] = [
  {
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
    risk_level: "HIGH",
    risk_score: 0.7,
    risk_rules_fired: [],
    what: null,
    where: null,
    why: null,
    how: null,
    recommended_action: null,
    reasoning_latency_ms: null,
  },
];

describe("ManagerView", () => {
  beforeEach(() => {
    vi.mocked(listSurveys).mockResolvedValue([survey]);
  });

  it("loads and lists surveys on mount", async () => {
    render(<ManagerView />);
    await waitFor(() => expect(screen.getByText("survey-1")).toBeInTheDocument());
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("shows an error message when the survey list fails to load", async () => {
    vi.mocked(listSurveys).mockReset();
    vi.mocked(listSurveys).mockRejectedValueOnce(new ApiError(500, "internal error"));
    render(<ManagerView />);
    await waitFor(() => expect(screen.getByText("internal error")).toBeInTheDocument());
  });

  it("loads a survey's summary and findings when its row is clicked", async () => {
    vi.mocked(getSurveySummary).mockResolvedValueOnce(summary);
    vi.mocked(getSurveyFindings).mockResolvedValueOnce(findings);
    const user = userEvent.setup();
    render(<ManagerView />);

    await waitFor(() => expect(screen.getByText("survey-1")).toBeInTheDocument());
    await user.click(screen.getByText("survey-1"));

    await waitFor(() => expect(screen.getByText(/2 findings/)).toBeInTheDocument());
    expect(getSurveySummary).toHaveBeenCalledWith("survey-1");
    expect(getSurveyFindings).toHaveBeenCalledWith("survey-1");
  });

  it("shows an error message when loading a survey's detail fails, and clears the loading indicator", async () => {
    vi.mocked(getSurveySummary).mockRejectedValueOnce(new ApiError(500, "internal error"));
    const user = userEvent.setup();
    render(<ManagerView />);

    await waitFor(() => expect(screen.getByText("survey-1")).toBeInTheDocument());
    await user.click(screen.getByText("survey-1"));

    await waitFor(() => expect(screen.getByText("internal error")).toBeInTheDocument());
    // The `finally { setLoading(false) }` path specifically: a version that
    // only cleared loading in the try-block would leave "Loading…" stuck
    // forever on this exact failure path.
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
  });

  it("refresh button re-fetches the survey list", async () => {
    const user = userEvent.setup();
    render(<ManagerView />);
    await waitFor(() => expect(listSurveys).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(listSurveys).toHaveBeenCalledTimes(2));
  });
});

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OperatorView } from "./OperatorView";
import { ApiError } from "../api/client";
import type { FindingEvent, SurveyRecord } from "../api/types";
import type { LiveFindings } from "../api/useLiveFindings";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, startSurvey: vi.fn(), stopSurvey: vi.fn() };
});
vi.mock("../api/useLiveFindings", () => ({ useLiveFindings: vi.fn() }));

import { startSurvey, stopSurvey } from "../api/client";
import { useLiveFindings } from "../api/useLiveFindings";

const record: SurveyRecord = {
  survey_id: "survey-1",
  line_id: "line_1",
  status: "running",
  source_type: "replay",
  capabilities: {
    has_calibrated_depth: false,
    has_real_position: false,
    has_true_amplitude: false,
    latency_class: "batch",
  },
  started_at: "2026-01-01T00:00:00Z",
  stopped_at: null,
};

const emptyLiveFindings: LiveFindings = { events: [], connectionState: "closed", clear: vi.fn() };

describe("OperatorView", () => {
  beforeEach(() => {
    vi.mocked(useLiveFindings).mockReturnValue(emptyLiveFindings);
  });

  it("starts a survey with the entered survey/line IDs and shows its status", async () => {
    vi.mocked(startSurvey).mockResolvedValueOnce(record);
    const user = userEvent.setup();
    render(<OperatorView />);

    await user.click(screen.getByRole("button", { name: "Start" }));

    await waitFor(() => expect(screen.getByText("running")).toBeInTheDocument());
    expect(startSurvey).toHaveBeenCalledWith("survey-1", "line_1");
  });

  it("subscribes the live-findings hook only once a survey is running", async () => {
    vi.mocked(startSurvey).mockResolvedValueOnce(record);
    const user = userEvent.setup();
    render(<OperatorView />);

    expect(useLiveFindings).toHaveBeenCalledWith(false);

    await user.click(screen.getByRole("button", { name: "Start" }));

    await waitFor(() => expect(useLiveFindings).toHaveBeenCalledWith(true));
  });

  it("shows the ApiError's message when start fails", async () => {
    vi.mocked(startSurvey).mockRejectedValueOnce(new ApiError(409, "survey already running"));
    const user = userEvent.setup();
    render(<OperatorView />);

    await user.click(screen.getByRole("button", { name: "Start" }));

    await waitFor(() => expect(screen.getByText("survey already running")).toBeInTheDocument());
  });

  it("stops a running survey and reflects the stopped status", async () => {
    vi.mocked(startSurvey).mockResolvedValueOnce(record);
    vi.mocked(stopSurvey).mockResolvedValueOnce({ ...record, status: "stopped" });
    const user = userEvent.setup();
    render(<OperatorView />);

    await user.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(screen.getByText("running")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.getByText("stopped")).toBeInTheDocument());
    expect(stopSurvey).toHaveBeenCalledWith("survey-1");
  });

  it("shows the ApiError's message when stop fails", async () => {
    vi.mocked(startSurvey).mockResolvedValueOnce(record);
    vi.mocked(stopSurvey).mockRejectedValueOnce(new ApiError(400, "survey is not running"));
    const user = userEvent.setup();
    render(<OperatorView />);

    await user.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(screen.getByText("running")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Stop" }));

    await waitFor(() => expect(screen.getByText("survey is not running")).toBeInTheDocument());
  });

  it("lets the operator edit the survey and line IDs before starting", async () => {
    vi.mocked(startSurvey).mockResolvedValueOnce(record);
    const user = userEvent.setup();
    render(<OperatorView />);

    await user.clear(screen.getByLabelText("Survey ID"));
    await user.type(screen.getByLabelText("Survey ID"), "custom-survey");
    await user.clear(screen.getByLabelText("Line ID"));
    await user.type(screen.getByLabelText("Line ID"), "custom-line");
    await user.click(screen.getByRole("button", { name: "Start" }));

    expect(startSurvey).toHaveBeenCalledWith("custom-survey", "custom-line");
  });

  it("renders each live-feed event as its own log line, labelled by type", () => {
    const events: FindingEvent[] = [
      {
        type: "finding.created",
        survey_id: "survey-1",
        finding: {
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
      },
    ];
    vi.mocked(useLiveFindings).mockReturnValue({ events, connectionState: "open", clear: vi.fn() });

    render(<OperatorView />);

    expect(screen.getByText("created")).toBeInTheDocument();
    expect(screen.getByText("cavities")).toBeInTheDocument();
  });

  it("disables the Start button while a survey is running", async () => {
    vi.mocked(startSurvey).mockResolvedValueOnce(record);
    const user = userEvent.setup();
    render(<OperatorView />);

    await user.click(screen.getByRole("button", { name: "Start" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Start" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "Stop" })).not.toBeDisabled();
  });
});

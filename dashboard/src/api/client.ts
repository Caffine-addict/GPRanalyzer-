// Thin REST wrapper over api/server.py. No auth: the backend has none today
// (an internal-tool trust model, documented in that session's review) — this
// client doesn't invent client-side auth either, since there'd be nothing
// real on the other end to check it.
import type { Finding, SurveyRecord, SurveySummary } from "./types";

const DEFAULT_BASE_URL = "http://127.0.0.1:8000";

export function apiBaseUrl(): string {
  return import.meta.env.VITE_API_BASE_URL ?? DEFAULT_BASE_URL;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, init);
  } catch (cause) {
    throw new ApiError(0, `Could not reach the API at ${apiBaseUrl()} — is the server running?`, { cause });
  }
  if (!response.ok) {
    let detail = "";
    try {
      const body = (await response.json()) as { detail?: string };
      detail = body.detail ?? "";
    } catch {
      // Not every error response is JSON — fall through with an empty detail.
    }
    throw new ApiError(response.status, detail || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function listSurveys(): Promise<SurveyRecord[]> {
  return request<SurveyRecord[]>("/surveys");
}

export function startSurvey(surveyId: string, lineId = "line_1"): Promise<SurveyRecord> {
  return request<SurveyRecord>(`/surveys/${encodeURIComponent(surveyId)}/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ line_id: lineId }),
  });
}

export function stopSurvey(surveyId: string): Promise<SurveyRecord> {
  return request<SurveyRecord>(`/surveys/${encodeURIComponent(surveyId)}/stop`, {
    method: "POST",
  });
}

export function getSurveyFindings(surveyId: string): Promise<Finding[]> {
  return request<Finding[]>(`/surveys/${encodeURIComponent(surveyId)}/findings`);
}

export function getSurveySummary(surveyId: string): Promise<SurveySummary> {
  return request<SurveySummary>(`/surveys/${encodeURIComponent(surveyId)}/summary`);
}

export function getLineFindings(lineId: string): Promise<Finding[]> {
  return request<Finding[]>(`/lines/${encodeURIComponent(lineId)}/findings`);
}

export function websocketUrl(): string {
  return apiBaseUrl().replace(/^http/, "ws") + "/ws/live";
}

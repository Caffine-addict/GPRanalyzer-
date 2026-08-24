import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  apiBaseUrl,
  getLineFindings,
  getSurveyFindings,
  getSurveySummary,
  listSurveys,
  startSurvey,
  stopSurvey,
  websocketUrl,
} from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api/client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("apiBaseUrl falls back to the default when VITE_API_BASE_URL is unset", () => {
    // Nothing in this test env sets VITE_API_BASE_URL, so it's genuinely
    // undefined here — an explicit vi.stubEnv(..., "") would instead test
    // the empty-string case, which `??` deliberately does NOT treat as unset.
    expect(apiBaseUrl()).toBe("http://127.0.0.1:8000");
  });

  it("apiBaseUrl uses VITE_API_BASE_URL when set", () => {
    vi.stubEnv("VITE_API_BASE_URL", "http://example.test:9000");
    expect(apiBaseUrl()).toBe("http://example.test:9000");
  });

  it("websocketUrl swaps the http(s) scheme for ws(s) and appends /ws/live", () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://example.test");
    expect(websocketUrl()).toBe("wss://example.test/ws/live");
  });

  it("listSurveys GETs /surveys and returns the parsed body", async () => {
    const surveys = [{ survey_id: "s1" }];
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(surveys));

    const result = await listSurveys();

    expect(result).toEqual(surveys);
    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("/surveys");
  });

  it("startSurvey POSTs to /surveys/{id}/start with a JSON line_id body", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ survey_id: "s1", status: "running" }));

    await startSurvey("s1", "line_9");

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("/surveys/s1/start");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ line_id: "line_9" });
  });

  it("startSurvey URL-encodes the survey_id", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({}));
    await startSurvey("has space/slash");
    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("/surveys/has%20space%2Fslash/start");
  });

  it.each([
    ["stopSurvey", () => stopSurvey("has space/slash"), "/surveys/has%20space%2Fslash/stop"],
    ["getSurveyFindings", () => getSurveyFindings("has space/slash"), "/surveys/has%20space%2Fslash/findings"],
    ["getSurveySummary", () => getSurveySummary("has space/slash"), "/surveys/has%20space%2Fslash/summary"],
    ["getLineFindings", () => getLineFindings("has space/slash"), "/lines/has%20space%2Fslash/findings"],
  ] as const)("%s URL-encodes its id parameter", async (_name, call, expectedPath) => {
    // startSurvey had a dedicated encoding test but the other four calls
    // didn't — an unencoded '/' in a survey/line ID would silently break
    // routing on any of them with nothing previously catching it.
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({}));
    await call();
    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain(expectedPath);
  });

  it("stopSurvey POSTs to /surveys/{id}/stop with no body", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ status: "stopped" }));
    await stopSurvey("s1");
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("/surveys/s1/stop");
    expect(init?.method).toBe("POST");
  });

  it("getSurveyFindings GETs /surveys/{id}/findings", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse([]));
    await getSurveyFindings("s1");
    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("/surveys/s1/findings");
  });

  it("getSurveySummary GETs /surveys/{id}/summary", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ total_findings: 0 }));
    await getSurveySummary("s1");
    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("/surveys/s1/summary");
  });

  it("getLineFindings GETs /lines/{id}/findings", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse([]));
    await getLineFindings("line_1");
    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("/lines/line_1/findings");
  });

  it("throws ApiError with the response's detail on a non-OK JSON response", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ detail: "survey already running" }, 409));

    await expect(startSurvey("s1")).rejects.toMatchObject(
      new ApiError(409, "survey already running"),
    );
  });

  it("falls back to the status text when a non-OK JSON response has no 'detail' key", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ other_field: "x" }), {
        status: 404,
        statusText: "Not Found",
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(listSurveys()).rejects.toThrow("404 Not Found");
  });

  it("falls back to the status text when a non-OK response has no JSON body", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response("not json", { status: 500, statusText: "Internal Server Error" }),
    );

    await expect(listSurveys()).rejects.toThrow("500 Internal Server Error");
  });

  it("wraps a network failure (fetch rejecting) in an ApiError with status 0", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("Failed to fetch"));

    const error = await listSurveys().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
    expect((error as ApiError).message).toContain("is the server running");
  });
});

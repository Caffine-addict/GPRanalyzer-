import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

vi.mock("./api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/client")>();
  return { ...actual, listSurveys: vi.fn().mockResolvedValue([]) };
});
vi.mock("./api/useLiveFindings", () => ({
  useLiveFindings: vi.fn().mockReturnValue({ events: [], connectionState: "closed", clear: vi.fn() }),
}));

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App routing", () => {
  it("redirects / to the operator view", () => {
    renderAt("/");
    expect(screen.getByRole("heading", { name: "Operator" })).toBeInTheDocument();
  });

  it("renders the manager view at /manager", () => {
    renderAt("/manager");
    expect(screen.getByRole("heading", { name: "Manager" })).toBeInTheDocument();
  });

  it("renders the pm view at /pm", () => {
    renderAt("/pm");
    expect(screen.getByRole("heading", { name: "PM" })).toBeInTheDocument();
  });

  it("always renders the nav bar", () => {
    renderAt("/operator");
    expect(screen.getByRole("link", { name: "Manager" })).toBeInTheDocument();
  });
});

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { Nav } from "./Nav";

describe("Nav", () => {
  it("renders a link to each of the three role views", () => {
    render(
      <MemoryRouter>
        <Nav />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Operator" })).toHaveAttribute("href", "/operator");
    expect(screen.getByRole("link", { name: "Manager" })).toHaveAttribute("href", "/manager");
    expect(screen.getByRole("link", { name: "PM" })).toHaveAttribute("href", "/pm");
  });

  it("marks the current route's link as active (bold)", () => {
    render(
      <MemoryRouter initialEntries={["/manager"]}>
        <Nav />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Manager" })).toHaveStyle({ fontWeight: 700 });
    expect(screen.getByRole("link", { name: "Operator" })).toHaveStyle({ fontWeight: 400 });
  });
});

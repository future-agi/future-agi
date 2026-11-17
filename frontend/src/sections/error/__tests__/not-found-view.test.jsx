import React from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";

import NotFoundView from "../not-found-view";

// The 404 page states plainly that the page does not exist and links home,
// with the same text on every visit.

describe("Not found page", () => {
  afterEach(() => vi.restoreAllMocks());

  it("says the page was not found and links home", () => {
    render(<NotFoundView />);
    expect(
      screen.getByRole("heading", { name: "Page not found" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /back to home/i })).toHaveAttribute(
      "href",
      "/",
    );
  });

  it("shows the same plain text on every visit", () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const first = render(<NotFoundView />).container.textContent;
    cleanup();
    vi.spyOn(Math, "random").mockReturnValue(0.999);
    const second = render(<NotFoundView />).container.textContent;
    expect(second).toBe(first);
    expect(second).not.toMatch(/deep space|mission log|sprint|half-baked/i);
    expect(second).not.toMatch(/[“”"]/);
  });
});

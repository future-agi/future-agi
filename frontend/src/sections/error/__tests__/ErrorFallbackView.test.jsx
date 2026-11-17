// The app-wide error fallback says plainly what happened and offers Retry and
// Go home: no joke lines ("Our hamsters ...", "Mission control spilled
// coffee ..."), no invented "mission log".
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

import ErrorFallbackView from "../ErrorFallbackView";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const JOKES =
  /hamster|coffee|houston|rocket|mission|nebula|galaxy|trajectory|cosmic|intern|spaghetti|horoscope/i;

describe("ErrorFallbackView", () => {
  it("states the error plainly, with no joke copy", () => {
    const { container } = render(
      <ErrorFallbackView resetErrorBoundary={vi.fn()} />,
    );
    expect(
      screen.getByRole("heading", { name: "Something went wrong" }),
    ).toBeInTheDocument();
    expect(container).toHaveTextContent(
      "This page hit an unexpected error and could not be shown.",
    );
    expect(container.textContent).not.toMatch(JOKES);
  });

  it("has no joke copy on any render (the old line was picked at random)", () => {
    for (let i = 0; i < 20; i += 1) {
      const { container, unmount } = render(<ErrorFallbackView />);
      expect(container.textContent).not.toMatch(JOKES);
      unmount();
    }
  });

  it("Retry resets the error boundary", () => {
    const reset = vi.fn();
    render(<ErrorFallbackView resetErrorBoundary={reset} />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("Go home is a link to the app's home page", () => {
    render(<ErrorFallbackView resetErrorBoundary={vi.fn()} />);
    expect(screen.getByRole("link", { name: "Go home" })).toHaveAttribute(
      "href",
      "/dashboard/develop",
    );
  });
});

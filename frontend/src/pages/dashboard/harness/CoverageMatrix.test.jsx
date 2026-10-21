import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import CoverageMatrix from "./CoverageMatrix";

const coverage = {
  scenarios: 50,
  placed: 50,
  axes: {
    task: { levels: 3, spread: 0.87, planned: 4, unused: ["quote_compare_surge"] },
    overlay: { levels: 6, spread: 0.82, planned: 6, unused: [] },
  },
  pairs: { "overlay x task": { covered: 47, possible: 110, masked: 0, share: 0.427 } },
  // The harness names the axes and levels it dealt, so the grid reads as a person would say it.
  labels: {
    axes: { task: "What they want done", overlay: "What makes it hard" },
    levels: {
      book_ride: "Book a ride",
      cancel_ride: "Cancel a ride",
      none: "Nothing in the way",
      prompt_injection: "Prompt injection",
      quote_compare_surge: "Compare a surge quote",
    },
  },
};

const scenarios = [
  { name: "a", coverage: { task: "book_ride", overlay: "none" } },
  { name: "b", coverage: { task: "book_ride", overlay: "prompt_injection" } },
  { name: "c", coverage: { task: "cancel_ride", overlay: "none" } },
];

describe("CoverageMatrix", () => {
  it("leads with the thinnest pairing rather than a scenario count", () => {
    render(<CoverageMatrix scenarios={scenarios} coverage={coverage} />);
    expect(screen.getByText(/Thinnest pairing is/)).toBeInTheDocument();
    expect(screen.getByText("overlay x task")).toBeInTheDocument();
    expect(screen.getByText("47 of 110")).toBeInTheDocument();
  });

  it("names the gap in words, because scanning a grid for blanks is work", () => {
    render(<CoverageMatrix scenarios={scenarios} coverage={coverage} />);
    // cancel_ride never meets prompt_injection in the three scenarios above.
    expect(screen.getByText(/Nothing covers these/)).toBeInTheDocument();
    // Each appears twice: once in the sentence, once as an axis label on the matrix.
    expect(screen.getAllByText("Cancel a ride").length).toBeGreaterThan(1);
    expect(screen.getAllByText("Prompt injection").length).toBeGreaterThan(1);
  });

  it("shows a level the plan dealt that no scenario ever used", () => {
    render(<CoverageMatrix scenarios={scenarios} coverage={coverage} />);
    expect(
      screen.getByText("What they want done: Compare a surge quote"),
    ).toBeInTheDocument();
  });

  it("says so plainly when no scenario carries a coordinate", () => {
    render(<CoverageMatrix scenarios={[{ name: "a" }]} coverage={{}} />);
    expect(screen.getByText(/nothing to cross-tabulate/)).toBeInTheDocument();
  });
});

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import CoverageMatrix from "./CoverageMatrix";

// Built from a real hosted run of 50 scenarios against the ride voice agent, job 9e6ea728: the plan
// dealt four task levels and the suite wrote three, and 47 of 110 overlay-by-task combinations exist.
const coverage = {
  scenarios: 50,
  placed: 50,
  axes: {
    task: { levels: 3, spread: 0.87, planned: 4, unused: ["quote_compare_surge"] },
    overlay: { levels: 6, spread: 0.82, planned: 6, unused: [] },
  },
  pairs: { "overlay x task": { covered: 47, possible: 110, masked: 0, share: 0.427 } },
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
    expect(screen.getAllByText("cancel_ride").length).toBeGreaterThan(1);
    expect(screen.getAllByText("prompt_injection").length).toBeGreaterThan(1);
  });

  it("shows a level the plan dealt that no scenario ever used", () => {
    render(<CoverageMatrix scenarios={scenarios} coverage={coverage} />);
    expect(screen.getByText("task: quote_compare_surge")).toBeInTheDocument();
  });

  it("says so plainly when no scenario carries a coordinate", () => {
    render(<CoverageMatrix scenarios={[{ name: "a" }]} coverage={{}} />);
    expect(screen.getByText(/nothing to cross-tabulate/)).toBeInTheDocument();
  });
});

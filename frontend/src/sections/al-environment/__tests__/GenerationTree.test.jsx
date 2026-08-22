import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";

import GenerationTree from "../GenerationTree";

const running = {
  state: "running",
  asked: 12,
  kept: 5,
  at_once: 6,
  slices: [
    { use_case: "Book a ride for a guest caller", wanted: 2, kept: 2, state: "done" },
    { use_case: "Book with a saved card after OTP", wanted: 2, kept: 1, state: "running" },
    { use_case: "Send a payment link SMS", wanted: 2, kept: 0, state: "waiting" },
  ],
};

describe("GenerationTree", () => {
  it("draws nothing until there is a fan-out to draw", () => {
    const { container } = render(<GenerationTree generation={null} />);
    expect(container).toBeEmptyDOMElement();
    const empty = render(<GenerationTree generation={{ state: "running", slices: [] }} />);
    expect(empty.container).toBeEmptyDOMElement();
  });

  it("says how much of the suite is done and how the writers are split", () => {
    render(<GenerationTree generation={running} />);

    expect(screen.getByText("5/12 scenarios")).toBeInTheDocument();
    // The question during the wait is whether anything is still happening, so the split of
    // writers is stated rather than left to be counted off the rows.
    expect(screen.getByText("1 writing, 1 done of 3")).toBeInTheDocument();
    expect(screen.getByText("Writing the suite")).toBeInTheDocument();
  });

  it("shows each use case with its own share", () => {
    render(<GenerationTree generation={running} />);

    expect(screen.getByText("Book a ride for a guest caller")).toBeInTheDocument();
    expect(screen.getByText("2/2")).toBeInTheDocument();
    expect(screen.getByText("1/2")).toBeInTheDocument();
    expect(screen.getByText("0/2")).toBeInTheDocument();
  });

  it("reads as finished once it has settled", () => {
    render(
      <GenerationTree
        generation={{
          ...running,
          state: "done",
          kept: 12,
          slices: running.slices.map((one) => ({ ...one, state: "done", kept: one.wanted })),
        }}
      />
    );

    expect(screen.getByText("Suite generated")).toBeInTheDocument();
    expect(screen.getByText("3 of 3 use cases")).toBeInTheDocument();
    // The note about proving is guidance for the wait; once it is over it is noise.
    expect(screen.queryByText(/proves its own scenarios/i)).not.toBeInTheDocument();
  });

  it("surfaces a writer that failed rather than leaving a gap", () => {
    render(
      <GenerationTree
        generation={{
          ...running,
          slices: [
            ...running.slices,
            { use_case: "Cancel a matched ride", wanted: 2, kept: 0, state: "failed" },
          ],
        }}
      />
    );

    expect(screen.getByText("· 1 failed")).toBeInTheDocument();
    expect(screen.getByText("Cancel a matched ride")).toBeInTheDocument();
  });
});

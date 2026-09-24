import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import coverageSample from "src/api/simulate-environments/_fixtures/scenarioSamples/07-coverage.json";
import CoverageMatrix from "../CoverageMatrix";

// The grid consumes the server coverage response through the hook; mock the hook
// so the component renders straight from a captured sample (07 = the 20-row run).
vi.mock("src/api/simulate-environments/scenariosHooks", () => ({
  useScenarioCoverage: vi.fn(),
}));
const { useScenarioCoverage } = await import("src/api/simulate-environments/scenariosHooks");

beforeEach(() => {
  useScenarioCoverage.mockReset();
  useScenarioCoverage.mockReturnValue({ data: coverageSample });
});

describe("CoverageMatrix", () => {
  it("restates the header summary from the server data (Axes / Pairs / Forced)", () => {
    render(<CoverageMatrix jobId="job-1" search="" filters={{}} />);
    // All 8 axes are varied (levels > 1) in the sample.
    expect(screen.getByText("8/8")).toBeInTheDocument();
    // All five forced overlays are present in the overlay axis.
    expect(screen.getByText("5/5")).toBeInTheDocument();
    // Subtitle scenario count comes off per_axis, not envState.
    expect(screen.getByText(/20 scenarios/)).toBeInTheDocument();
  });

  it("passes the search + filters through to the coverage hook", () => {
    render(<CoverageMatrix jobId="job-1" search="ride" filters={{ "persona.accent": ["Canadian"] }} />);
    expect(useScenarioCoverage).toHaveBeenCalledWith(
      "job-1",
      expect.objectContaining({
        search: "ride",
        filters: { "persona.accent": ["Canadian"] },
      }),
    );
  });

  it("draws the pairwise grid from rows / columns / cells, zeros included", () => {
    render(<CoverageMatrix jobId="job-1" search="" filters={{}} defaultExpanded />);
    // Row-axis levels (humanized) and column-axis levels render as the grid axes.
    expect(screen.getByText("Authenticate Otp")).toBeInTheDocument();
    expect(screen.getByText("Prompt Injection")).toBeInTheDocument();
    // The forced-overlays checklist reads from per_axis.overlay.counts.
    expect(screen.getByText("Destructive / irreversible")).toBeInTheDocument();
  });

  it("shows an error state instead of a zero-coverage grid when the request fails", () => {
    // Without this, a failed request renders 0/8 axes and 0% pairs — falsely
    // telling the user their suite covers nothing.
    const refetch = vi.fn();
    useScenarioCoverage.mockReturnValue({ data: undefined, isError: true, refetch });
    render(<CoverageMatrix jobId="job-1" search="" filters={{}} />);
    expect(screen.getByText(/Couldn't load coverage/i)).toBeInTheDocument();
    expect(screen.queryByText("8/8")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("shows a loading state, not zero coverage, while the first response is in flight", () => {
    useScenarioCoverage.mockReturnValue({ data: undefined, isPending: true });
    render(<CoverageMatrix jobId="job-1" search="" filters={{}} />);
    expect(screen.getByLabelText("Loading coverage")).toBeInTheDocument();
    expect(screen.queryByText(/0 scenarios/)).toBeNull();
    expect(screen.queryByText("0%")).toBeNull();
  });
});

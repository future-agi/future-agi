import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";

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
});

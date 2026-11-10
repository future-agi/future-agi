import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { CellSkeleton, Field } from "../traceCells";

describe("Field (persona cell attribute)", () => {
  it("wraps a long value instead of cutting it off", () => {
    const traits = "Detail-oriented, Questioning, Anxious, Impatient, Talks over the agent";
    render(<Field icon="solar:tag-linear" label="Traits" value={traits} />);
    const value = screen.getByText(traits);
    expect(getComputedStyle(value).whiteSpace).not.toBe("nowrap");
    expect(getComputedStyle(value).textOverflow).not.toBe("ellipsis");
  });

  it("renders nothing for an empty value", () => {
    const { container } = render(<Field icon="solar:tag-linear" label="Traits" value="" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("CellSkeleton", () => {
  afterEach(() => vi.restoreAllMocks());

  // Each bar starts its shimmer cycle when it mounts; offsetting it by how far
  // the page clock is into a cycle keeps bars that mount later in step.
  const phaseAt = (now) => {
    vi.spyOn(performance, "now").mockReturnValue(now);
    const { container, unmount } = render(<CellSkeleton />);
    const phase = container
      .querySelector(".MuiSkeleton-root")
      .style.getPropertyValue("--skeleton-phase");
    unmount();
    return phase;
  };

  it("starts each bar at the page clock's point in the 2s shimmer cycle", () => {
    expect(phaseAt(12345)).toBe("-345ms");
  });

  it("gives bars mounted a whole number of cycles apart the same phase", () => {
    const early = phaseAt(1880);
    expect(early).toBe("-1880ms");
    expect(phaseAt(1880 + 2000 * 7)).toBe(early);
  });
});

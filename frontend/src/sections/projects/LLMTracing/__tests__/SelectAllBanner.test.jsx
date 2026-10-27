import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import SelectAllBanner from "../SelectAllBanner";

describe("SelectAllBanner", () => {
  const defaultProps = {
    visible: true,
    visibleCount: 25,
    noun: "trace",
    onSelectAll: vi.fn(),
  };

  it("preserves the exact-total label for exact metadata", () => {
    render(<SelectAllBanner {...defaultProps} totalMatching={42} />);

    expect(
      screen.getByRole("button", {
        name: "Select all 42 traces matching your filter",
      }),
    ).toBeInTheDocument();
  });

  it("labels a lower bound without presenting it as the exact total", () => {
    render(
      <SelectAllBanner
        {...defaultProps}
        totalMatching={26}
        totalMatchingIsLowerBound
      />,
    );

    expect(
      screen.getByRole("button", {
        name: "Select all matching traces (≥26)",
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: "Select all 26 traces matching your filter",
      }),
    ).not.toBeInTheDocument();
  });
});

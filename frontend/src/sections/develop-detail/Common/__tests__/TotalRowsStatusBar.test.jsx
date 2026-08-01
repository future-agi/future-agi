import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import TotalRowsStatusBar from "../TotalRowsStatusBar";

function makeApi({ total = 25, lowerBound = false } = {}) {
  return {
    totalRowCount: total,
    totalRowCountIsLowerBound: lowerBound,
    getFirstDisplayedRowIndex: () => 0,
    getLastDisplayedRowIndex: () => 4,
    getDisplayedRowAtIndex: (index) => ({ data: { id: index }, stub: false }),
    getDisplayedRowCount: () => 5,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    isDestroyed: () => false,
  };
}

describe("TotalRowsStatusBar", () => {
  it("labels a proven lower-bound total with a plus sign", async () => {
    render(<TotalRowsStatusBar api={makeApi({ lowerBound: true })} />);

    expect(await screen.findByText("Viewing: 5/25+ rows")).toBeInTheDocument();
  });

  it("keeps the existing exact-total rendering", async () => {
    render(<TotalRowsStatusBar api={makeApi()} />);

    expect(await screen.findByText("Viewing: 5/25 rows")).toBeInTheDocument();
  });
});

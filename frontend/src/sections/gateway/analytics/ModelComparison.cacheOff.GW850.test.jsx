import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import ModelComparison from "./ModelComparison";
import { grid } from "../logs/__tests__/fakeAgGrid";

vi.mock("ag-grid-react", () => import("../logs/__tests__/fakeAgGrid"));
vi.mock("./hooks/useAnalyticsModels", () => ({
  useAnalyticsModels: () => ({
    data: {
      models: [
        {
          model: "fixture-model",
          provider: "fixture-provider",
          request_count: 4,
          cache_hit_rate: 25,
        },
      ],
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

// GW850-004: copied from the hash-verified activation record, not product code.
const CACHE_OFF_NOTE =
  "Response caching is off on this gateway. Cache hits reflect the selected period.";
const cacheColumn = () =>
  grid.props.columnDefs.find((column) => column.field === "cache_hit_rate");

describe("GW850-004 Models cache-off placement", () => {
  it("shows the exact note below the filters and directly above the grid without hover, retaining the tooltip", () => {
    render(<ModelComparison cacheOff />);
    expect(cacheColumn().headerTooltip).toBe(CACHE_OFF_NOTE);
    const note = screen.getByText(CACHE_OFF_NOTE);
    expect(note).toBeVisible();
    expect(note.textContent).toBe(CACHE_OFF_NOTE);
    expect(note.previousElementSibling).toBe(screen.getByRole("search"));
    expect(note.nextElementSibling).toContainElement(
      screen.getByTestId("ag-grid"),
    );
  });

  it("shows neither the note nor the cache-off tooltip while caching is on", () => {
    render(<ModelComparison cacheOff={false} />);
    expect(screen.queryByText(CACHE_OFF_NOTE)).not.toBeInTheDocument();
    expect(cacheColumn().headerTooltip).toBeUndefined();
  });
});

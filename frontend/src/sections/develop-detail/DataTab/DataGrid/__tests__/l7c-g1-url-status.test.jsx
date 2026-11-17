// L7c g1 (url:grid-filters): a `?filters=` link's Status / Tags pill filters
// the rows: hydration bumps `hydratedAt` (the grid's re-query key) and the
// grid's API filters carry system:status / system:tags.
import React from "react";
import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import {
  getDataGridFilters,
  getDataGridView,
  useDataGridUrlSync,
} from "../useDataGridView";

const link = (list) =>
  `/dashboard/develop/ds-1?tab=data&filters=${encodeURIComponent(JSON.stringify(list))}`;
const hydrate = (datasetId, list) =>
  renderHook(() => useDataGridUrlSync(datasetId, []), {
    wrapper: ({ children }) => (
      <MemoryRouter initialEntries={[link(list)]}>{children}</MemoryRouter>
    ),
  });

describe("url:grid-filters (L7c g1)", () => {
  it("hydrates a failed-evaluation link without a split catalog", () => {
    renderHook(() => useDataGridUrlSync("ds-failed-no-splits", undefined), {
      wrapper: ({ children }) => (
        <MemoryRouter
          initialEntries={[
            "/dashboard/develop/ds-failed-no-splits?failed=1&fevals=quality",
          ]}
        >
          {children}
        </MemoryRouter>
      ),
    });
    expect(getDataGridView("ds-failed-no-splits")).toMatchObject({
      failedOnly: true,
      failedEvalIds: ["quality"],
    });
    expect(getDataGridFilters("ds-failed-no-splits")).toContainEqual({
      column_id: "system:row_failed_in",
      filter_config: {
        filter_type: "categorical",
        filter_op: "in",
        filter_value: ["quality"],
      },
    });
  });

  it("a Status link re-queries the grid with system:status", () => {
    hydrate("ds-status", [
      {
        column_id: "system:status",
        filter_config: {
          filter_type: "categorical",
          filter_op: "in",
          filter_value: ["needs_review"],
        },
      },
    ]);
    const view = getDataGridView("ds-status");
    expect(view.statuses).toEqual(["needs_review"]);
    expect(view.hydratedAt).toBeGreaterThan(0);
    expect(
      getDataGridFilters("ds-status").find(
        (f) => f.column_id === "system:status",
      )?.filter_config.filter_value,
    ).toEqual(["needs_review"]);
  });

  it("a Tags link re-queries the grid with system:tags", () => {
    hydrate("ds-tags", [
      {
        column_id: "system:tags",
        filter_config: {
          filter_type: "categorical",
          filter_op: "in",
          filter_value: ["refund"],
        },
      },
    ]);
    const view = getDataGridView("ds-tags");
    expect(view.tags).toEqual(["refund"]);
    expect(view.hydratedAt).toBeGreaterThan(0);
    expect(
      getDataGridFilters("ds-tags").some((f) => f.column_id === "system:tags"),
    ).toBe(true);
  });
});

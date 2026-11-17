// F49: a view without splits replaces the split filter an earlier view
// resolved. Applying it clears both the filter and `splits=`, or the URL
// write-back would bring the earlier split back.
import React from "react";
import { act, renderHook } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import useDataToolbarView from "../useDataToolbarView";
import {
  getDataGridFilters,
  getDataGridView,
  useDataGridUrlSync,
} from "../useDataGridView";

const shown = vi.hoisted(
  () => new Set(["dataset.savedViews", "dataset.splits"]),
);

vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: (id) => shown.has(id),
}));
vi.mock(
  "src/sections/common/ux/oneview/layoutPrefs",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useLayoutPrefs: () => [{}, () => {}],
  }),
);

const CATALOG = [
  { id: "split-train", name: "train" },
  { id: "split-test", name: "test" },
];
const VIEW = { q: "", density: "compact", filters: [], sort: [] };
const splitFilters = (id) =>
  getDataGridFilters(id).filter((f) => f.column_id === "system:splits");

describe("applying a view without splits", () => {
  it("clears the split filter a previous view resolved", () => {
    const id = "ds-split-clear";
    const wrapper = ({ children }) =>
      React.createElement(
        MemoryRouter,
        { initialEntries: [`/dashboard/develop/${id}`] },
        children,
      );
    const { result, rerender } = renderHook(
      ({ splits }) => {
        useDataGridUrlSync(id, splits);
        const [params] = useSearchParams();
        return { toolbar: useDataToolbarView(id), params };
      },
      { wrapper, initialProps: { splits: undefined } },
    );

    // A train-filtered view, resolved once the split catalog loads.
    act(() => result.current.toolbar.applyView({ ...VIEW, splits: "train" }));
    rerender({ splits: CATALOG });
    expect(getDataGridView(id)).toMatchObject({
      splitIds: ["split-train"],
      pendingSplitNames: null,
    });
    expect(splitFilters(id)).toHaveLength(1);
    expect(result.current.params.get("splits")).toBe("train");

    // Then a view without splits.
    act(() => result.current.toolbar.applyView({ ...VIEW }));
    expect(getDataGridView(id).splitIds).toEqual([]);
    expect(splitFilters(id)).toEqual([]);
    expect(result.current.params.has("splits")).toBe(false);
  });
});

// F49: a copied grid view keeps its split selection (`splits=train`). The
// link carries the split names, and the grid filters by them once the split
// catalog is known.
import React from "react";
import { act, renderHook } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { toolbarViewState } from "../useDataToolbarView";
import {
  GRID_VIEW_PARAM,
  gridViewHref,
  readGridViewLink,
} from "../gridViewLink";
import {
  getDataGridFilters,
  getDataGridView,
  useDataGridUrlSync,
  useDataGridView,
} from "../useDataGridView";

const PAGE = "http://localhost/dashboard/develop/dataset";
const CATALOG = [
  { id: "split-train", name: "train" },
  { id: "split-test", name: "test" },
];
const copyLink = (state) =>
  new URL(gridViewHref(state, {}, `${PAGE}?splits=old`));
const open = (url) => readGridViewLink(url.searchParams.get(GRID_VIEW_PARAM));

describe("grid view links keep the split filter", () => {
  it("carries the selected splits of a copied view", () => {
    const url = copyLink(toolbarViewState({ splits: "train" }));
    expect(url.searchParams.has("splits")).toBe(false);
    expect(open(url)).toMatchObject({ splits: "train" });
    expect(open(copyLink({ splits: "hard,none" })).splits).toBe("hard,none");
  });

  it("reads links without splits as before and refuses a malformed split list", () => {
    expect(open(copyLink(toolbarViewState()))).not.toHaveProperty("splits");
    const view = { q: "", density: "compact", filters: [], sort: [] };
    expect(
      readGridViewLink(JSON.stringify({ ...view, splits: ["train"] })),
    ).toBeNull();
  });

  it("filters the grid by the linked splits once the split catalog loads", () => {
    const id = "ds-split-link";
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
        return { view: useDataGridView(id), params };
      },
      { wrapper, initialProps: { splits: undefined } },
    );
    act(() => result.current.view.setPendingSplitNames("train"));
    expect(getDataGridView(id).splitIds).toEqual([]);

    rerender({ splits: CATALOG });
    expect(getDataGridView(id)).toMatchObject({
      splitIds: ["split-train"],
      pendingSplitNames: null,
    });
    expect(getDataGridFilters(id)).toContainEqual({
      column_id: "system:splits",
      filter_config: {
        filter_type: "categorical",
        filter_op: "in",
        filter_value: ["split-train"],
      },
    });
    expect(result.current.params.get("splits")).toBe("train");
  });
});

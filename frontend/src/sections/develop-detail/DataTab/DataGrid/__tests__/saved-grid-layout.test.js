import { describe, expect, it } from "vitest";
import { toolbarViewModified, toolbarViewState } from "../useDataToolbarView";
import {
  GRID_VIEW_PARAM,
  gridViewHref,
  readGridViewLink,
} from "../gridViewLink";

const sort = [{ colId: "column", sort: "desc" }];
const filters = [
  {
    columnId: "column",
    filterConfig: {
      filterType: "text",
      filterOp: "contains",
      filterValue: "keep",
    },
  },
];

describe("persisted grid configuration", () => {
  it("captures no-value predicates without treating them as unfinished filters", () => {
    const empty = {
      columnId: "column",
      filterConfig: {
        filterType: "text",
        filterOp: "is_null",
        filterValue: "",
      },
    };
    expect(toolbarViewState({ filters: [empty] }).filters).toEqual([empty]);
  });
  it("captures sorting even when every other preference is at its default", () => {
    const state = toolbarViewState({ gridView: { sort } });
    expect(state.sort).toEqual(sort);
    expect(toolbarViewModified(state)).toBe(true);
    expect(toolbarViewModified(toolbarViewState())).toBe(false);
  });

  it("encodes complete filter objects and column layout in a reusable link", () => {
    const state = toolbarViewState({
      search: "keep",
      filters,
      gridView: { sort, density: "tall" },
    });
    const layout = {
      columns: { visible: { other: false }, order: ["column", "other"] },
    };
    const href = gridViewHref(
      state,
      layout,
      "http://localhost/dashboard/develop/dataset?q=old&failed=1&expr=old",
    );
    const url = new URL(href);
    expect(url.pathname).toBe("/dashboard/develop/dataset");
    expect(url.searchParams.has("expr")).toBe(false);
    expect(url.searchParams.has("failed")).toBe(false);
    expect(readGridViewLink(url.searchParams.get(GRID_VIEW_PARAM))).toEqual({
      q: "keep",
      density: "tall",
      sort,
      filters,
      layout,
      failed: false,
      fevals: [],
    });
  });

  it("restores expressions but rejects malformed links and ignores unsupported fields", () => {
    expect(readGridViewLink("not json")).toBeNull();
    expect(
      readGridViewLink(
        JSON.stringify({ density: [], q: "", filters: [], sort: [] }),
      ),
    ).toBeNull();
    const linked = readGridViewLink(
      JSON.stringify({
        density: "compact",
        q: "",
        filters: [],
        sort: [],
        failed: true,
        expr: "len(column) > 12",
        statuses: ["approved"],
        layout: {},
      }),
    );
    expect(linked).toEqual({
      density: "compact",
      q: "",
      filters: [],
      sort: [],
      layout: { columns: {} },
      failed: true,
      fevals: [],
      expr: "len(column) > 12",
    });
  });

  it.each([null, false, 7, ["column = 1"], { text: "column = 1" }])(
    "rejects a non-string linked expression (%j)",
    (expr) => {
      expect(
        readGridViewLink(
          JSON.stringify({
            q: "",
            density: "compact",
            filters: [],
            sort: [],
            expr,
          }),
        ),
      ).toBeNull();
    },
  );

  it.each([undefined, ""])(
    "keeps an absent or cleared expression out of copied links (%s)",
    (expr) => {
      const url = new URL(
        gridViewHref({ expr }, {}, "http://localhost/develop/dataset?expr=old"),
      );
      expect(url.searchParams.has("expr")).toBe(false);
      expect(readGridViewLink(url.searchParams.get(GRID_VIEW_PARAM))).toEqual({
        q: "",
        density: "compact",
        filters: [],
        sort: [],
        failed: false,
        fevals: [],
        layout: { columns: {} },
      });
    },
  );
});

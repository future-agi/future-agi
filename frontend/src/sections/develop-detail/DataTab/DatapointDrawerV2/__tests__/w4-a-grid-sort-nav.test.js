import { createElement, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios, { endpoints } from "src/utils/axios";
import { fetchRowInView } from "src/api/develop/row-view";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import {
  isRealBackend,
  setRealBackendForTests,
} from "src/sections/develop/realBackendCaps";
import { gridNavQuery, sortFromColumnState } from "../gridNavQuery";
import useRowNavigation from "../useRowNavigation";

const h = vi.hoisted(() => ({
  api: null,
  rows: [],
  filters: [],
  search: "",
  gridView: {
    failedOnly: false,
    failedEvalIds: [],
    splitIds: [],
    cellErrorOnly: false,
  },
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    default: { ...real.default, post: vi.fn(), get: vi.fn() },
  };
});
vi.mock("src/api/develop/dataset-versions", async (importOriginal) => ({
  ...(await importOriginal()),
  useAsOfVersion: () => ({ isAsOf: false, isLoading: false, version: null }),
}));
vi.mock("../../Versions/versionedSource", () => ({ useAsOfView: () => ({}) }));
vi.mock("src/sections/develop-detail/states", () => ({
  useDevelopFilterStore: (select) => select({ filters: h.filters }),
  useDevelopSearchStore: (select) => select({ search: h.search }),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ gridApi: { current: h.api } }),
}));
vi.mock("../../DataGrid/useDataGridView", () => ({
  useDataGridView: () => h.gridView,
}));

const DATASET = "11111111-1111-4111-8111-111111111111";
const A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const R1 = "22222222-2222-4222-8222-222222222221";
const R2 = "22222222-2222-4222-8222-222222222222";
const R3 = "22222222-2222-4222-8222-222222222223";
const ROWS = [
  { row_id: R1, [A]: 1, [B]: 2 },
  { row_id: R2, [A]: 2, [B]: 1 },
  { row_id: R3, [A]: 3, [B]: 3 },
];
const NULLABLE_ROWS = ROWS.map((row) =>
  row.row_id === R2 ? { ...row, [B]: null } : row,
);
const asc = (columnId) => ({ column_id: columnId, type: "ascending" });
const desc = (columnId) => ({ column_id: columnId, type: "descending" });
const MULTI_SORT = Object.freeze([
  Object.freeze({ colId: A, sort: "asc", sortIndex: 1 }),
  Object.freeze({ colId: B, sort: "asc", sortIndex: 0 }),
]);

function scalarOrder(rows, { column_id: columnId, type }) {
  const direction = type === "descending" ? -1 : 1;
  // Both columns are numeric. The shipped cell subquery excludes null or
  // non-numeric values before applying each scalar order_by(value, id).
  return rows
    .filter((row) => /^-?\d*\.?\d+$/.test(String(row[columnId])))
    .sort(
      (left, right) =>
        direction * (left[columnId] - right[columnId]) ||
        left.row_id.localeCompare(right.row_id),
    );
}

// Each earlier sort's eligibility filter survives the next scalar order.
// Each test supplies the table sortModel order independently of column state.
function hostedTableRows(sortModel) {
  return sortModel.reduce((rows, sort) => scalarOrder(rows, sort), h.rows);
}

let client;
beforeEach(() => {
  setRealBackendForTests(null);
  setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
  h.api = null;
  h.rows = ROWS;
  h.filters = [];
  h.search = "";
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  axios.post.mockReset();
  axios.get.mockReset();
  axios.post.mockImplementation(async (url, body) => {
    expect(url).toBe(endpoints.develop.getRowData(DATASET));
    // DatasetRowDataRequestSerializer.sort has max_length=1 on both bases.
    if (body.sort.length > 1) {
      throw Object.assign(new Error("Sort must contain at most one item"), {
        response: { status: 400 },
      });
    }
    const rows = body.sort.length ? scalarOrder(h.rows, body.sort[0]) : h.rows;
    const index = rows.findIndex((row) => row.row_id === body.row_id);
    expect(index).toBeGreaterThanOrEqual(0);
    return {
      data: {
        status: true,
        result: {
          current: { ...rows[index], order: index },
          next: { row_id: rows.slice(index + 1).map((row) => row.row_id) },
          // The hosted response omits prev; the hook uses the grid for it.
        },
      },
    };
  });
  axios.get.mockImplementation(async (url) => {
    expect(url).toBe(`${endpoints.develop.splits.list(DATASET)}membership/`);
    return { data: { result: { split_ids: [] } } };
  });
});

afterEach(() => {
  cleanup();
  client.clear();
  setLaunchOverrideForTest(null);
  setRealBackendForTests(null);
});

/* eslint-disable react/prop-types */
const Wrapper = ({ children }) =>
  createElement(
    QueryClientProvider,
    { client },
    createElement(MemoryRouter, null, children),
  );
/* eslint-enable react/prop-types */

function mountNavigation(columnState, tableRows, { loadedIndices } = {}) {
  let state = columnState;
  const nodes = new Map();
  const put = (data, rowIndex) =>
    nodes.set(rowIndex, { id: data.row_id, rowIndex, data });
  (loadedIndices ?? tableRows.map((_, index) => index)).forEach((index) =>
    put(tableRows[index], index),
  );
  const listeners = new Map();
  const emit = (event) => listeners.get(event)?.forEach((fn) => fn());
  const requests = [];
  const pending = [];
  let selectRow;
  const openRow = vi.fn((id) => selectRow(id));
  const api = {
    getColumnState: () => state,
    getRowNode: (id) => [...nodes.values()].find((node) => node.id === id),
    getDisplayedRowAtIndex: (index) =>
      nodes.get(index) ??
      (index >= 0 && index < tableRows.length
        ? { id: `stub-${index}`, rowIndex: index, stub: true }
        : undefined),
    getDisplayedRowCount: () => tableRows.length,
    addEventListener: (event, fn) => {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event).add(fn);
    },
    removeEventListener: (event, fn) => listeners.get(event)?.delete(fn),
    // A one-row block fixture: scrolling asks the grid datasource for the
    // full sortModel, exactly as DevelopDataV2.getDataSource does. Response
    // delivery is explicit, so assertions cover the not-yet-loaded seam.
    ensureIndexVisible: vi.fn((index) => {
      const sort = state
        .filter((column) => column.sort)
        .sort((a, b) => (a.sortIndex ?? 0) - (b.sortIndex ?? 0))
        .map(({ colId, sort: direction }) =>
          direction === "asc" ? asc(colId) : desc(colId),
        );
      const request = { index, sort };
      requests.push(request);
      pending.push(request);
    }),
  };
  h.api = api;
  const hook = renderHook(
    () => {
      const [rowId, setRowId] = useState(tableRows[0].row_id);
      selectRow = setRowId;
      return useRowNavigation({ datasetId: DATASET, rowId, openRow });
    },
    { wrapper: Wrapper },
  );
  return {
    ...hook,
    api,
    openRow,
    requests,
    selectRow: (id) => selectRow(id),
    setSort: (next) => {
      state = next;
      emit("sortChanged");
    },
    evict: (index) => {
      nodes.delete(index);
      emit("modelUpdated");
    },
    finishBlocks: () => {
      pending.splice(0).forEach(({ index, sort }) => {
        const data = hostedTableRows(sort)[index];
        if (data) put(data, index);
      });
      emit("modelUpdated");
    },
  };
}

const expectLoaded = async (result, rowId) => {
  await waitFor(() => {
    expect(result.current.loaded || result.current.isError).toBe(true);
  });
  expect(result.current.isError).toBe(false);
  await waitFor(() => {
    expect(result.current.loaded).toBe(true);
    expect(result.current.row.row_id).toBe(rowId);
  });
};

const expectScalarReads = (sort) => {
  for (const [, body] of axios.post.mock.calls) expect(body.sort).toEqual([sort]);
};

describe("W4-A-004 hosted grid sort navigation", () => {
  it("keeps the strict row endpoint fixture rejecting an unprojected sort list", async () => {
    await expect(
      fetchRowInView(DATASET, R1, { filters: [], sort: [asc(A), asc(B)] }),
    ).rejects.toMatchObject({ response: { status: 400 } });
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  it.each([
    {
      name: "physical A,B with B first and A last ascending",
      state: MULTI_SORT,
      tableSort: [asc(B), asc(A)],
      expected: [R1, R2, R3],
      effectiveSort: asc(A),
    },
    {
      name: "physical A,B with B first and A last descending",
      state: [
        { colId: A, sort: "desc", sortIndex: 1 },
        { colId: B, sort: "asc", sortIndex: 0 },
      ],
      tableSort: [asc(B), desc(A)],
      expected: [R3, R2, R1],
      effectiveSort: desc(A),
    },
    {
      name: "physical A,B with A first and B last ascending",
      state: [
        { colId: A, sort: "asc", sortIndex: 0 },
        { colId: B, sort: "asc", sortIndex: 1 },
      ],
      tableSort: [asc(A), asc(B)],
      expected: [R2, R1, R3],
      effectiveSort: asc(B),
    },
  ])("opens and moves to the adjacent table row: $name", async ({
    state,
    tableSort,
    expected,
    effectiveSort,
  }) => {
    expect(isRealBackend()).toBe(true);
    const snapshot = JSON.stringify(state);
    const tableRows = hostedTableRows(tableSort);
    expect(tableRows.map((row) => row.row_id)).toEqual(expected);
    const { result } = mountNavigation(state, tableRows);
    // RED on FINAL-1f: the real hook posts two sorts and gets the stub's 400.
    await expectLoaded(result, expected[0]);
    expect(axios.post).toHaveBeenNthCalledWith(
      1,
      endpoints.develop.getRowData(DATASET),
      { row_id: expected[0], filters: [], sort: [effectiveSort] },
    );
    expect(result.current.nextDisabled).toBe(false);
    act(() => result.current.goNext());
    await expectLoaded(result, expected[1]);
    expect(result.current.prevDisabled).toBe(false);
    act(() => result.current.goPrev());
    await expectLoaded(result, expected[0]);
    expectScalarReads(effectiveSort);
    expect(JSON.stringify(state)).toBe(snapshot);
  });

  it("keeps nullable numeric sort eligibility: R1 -> R3 -> R1", async () => {
    h.rows = NULLABLE_ROWS;
    const tableRows = hostedTableRows([asc(B), asc(A)]);
    expect(tableRows.map((row) => row.row_id)).toEqual([R1, R3]);
    const { result, openRow } = mountNavigation(MULTI_SORT, tableRows);
    await expectLoaded(result, R1);
    // r1 returns this endpoint adjacency, which includes the excluded R2.
    expect(result.current.view.next).toEqual([R2, R3]);
    expect(result.current.prevDisabled).toBe(true);
    act(() => result.current.goNext());
    await expectLoaded(result, R3);
    expect(result.current.nextDisabled).toBe(true);
    expect(result.current.prevDisabled).toBe(false);
    act(() => result.current.goPrev());
    await expectLoaded(result, R1);
    expect(openRow.mock.calls.map(([id]) => id)).toEqual([R3, R1]);
    expectScalarReads(asc(A));
  });

  it("loads adjacent full-sort table blocks in both directions without opening a stub", async () => {
    h.rows = NULLABLE_ROWS;
    const tableRows = hostedTableRows([asc(B), asc(A)]);
    const nav = mountNavigation(MULTI_SORT, tableRows, { loadedIndices: [0] });
    await expectLoaded(nav.result, R1);
    expect(nav.result.current.nextDisabled).toBe(false);
    act(() => nav.result.current.goNext());
    expect(nav.api.ensureIndexVisible).toHaveBeenCalledWith(1, "middle");
    expect(nav.openRow).not.toHaveBeenCalled();
    expect(nav.result.current.row.row_id).toBe(R1);
    act(() => nav.finishBlocks());
    await expectLoaded(nav.result, R3);
    act(() => nav.evict(0));
    expect(nav.result.current.prevDisabled).toBe(false);
    act(() => nav.result.current.goPrev());
    expect(nav.api.ensureIndexVisible).toHaveBeenLastCalledWith(0, "middle");
    expect(nav.openRow.mock.calls.map(([id]) => id)).toEqual([R3]);
    act(() => nav.finishBlocks());
    await expectLoaded(nav.result, R1);
    expect(nav.requests).toEqual([
      { index: 1, sort: [asc(B), asc(A)] },
      { index: 0, sort: [asc(B), asc(A)] },
    ]);
    expect(nav.openRow.mock.calls.map(([id]) => id)).toEqual([R3, R1]);
    expectScalarReads(asc(A));
  });

  it("cancels a pending block navigation when the full sort changes", async () => {
    h.rows = NULLABLE_ROWS;
    const nav = mountNavigation(MULTI_SORT, hostedTableRows([asc(B), asc(A)]), {
      loadedIndices: [0],
    });
    await expectLoaded(nav.result, R1);
    act(() => nav.result.current.goNext());
    expect(nav.requests).toHaveLength(1);
    // Same scalar A read, but a different complete table query.
    act(() => nav.setSort([
      { colId: A, sort: "asc", sortIndex: 1 },
      { colId: B, sort: "desc", sortIndex: 0 },
    ]));
    act(() => nav.finishBlocks());
    expect(nav.openRow).not.toHaveBeenCalled();
    expect(nav.result.current.row.row_id).toBe(R1);
  });

  it("cancels a pending block navigation when another row opens", async () => {
    h.rows = NULLABLE_ROWS;
    const nav = mountNavigation(MULTI_SORT, hostedTableRows([asc(B), asc(A)]), {
      loadedIndices: [0],
    });
    await expectLoaded(nav.result, R1);
    act(() => nav.result.current.goNext());
    expect(nav.requests).toHaveLength(1);
    act(() => nav.selectRow(R3));
    await expectLoaded(nav.result, R3);
    act(() => nav.finishBlocks());
    expect(nav.openRow).not.toHaveBeenCalled();
    expect(nav.result.current.row.row_id).toBe(R3);
  });

  it("does not fall back to scalar adjacency without the current grid node", async () => {
    h.rows = NULLABLE_ROWS;
    const nav = mountNavigation(MULTI_SORT, hostedTableRows([asc(B), asc(A)]), {
      loadedIndices: [],
    });
    await expectLoaded(nav.result, R1);
    expect(nav.result.current.view.next).toEqual([R2, R3]);
    expect(nav.result.current.prevDisabled).toBe(true);
    expect(nav.result.current.nextDisabled).toBe(true);
    act(() => nav.result.current.goNext());
    expect(nav.openRow).not.toHaveBeenCalled();
    expect(nav.requests).toEqual([]);
  });

  it.each([
    { name: "no sort", state: [], sort: [], first: R1, next: R2 },
    {
      name: "single ascending sort",
      state: [{ colId: A }, { colId: B, sort: "asc", sortIndex: 0 }],
      sort: [asc(B)], first: R2, next: R1,
    },
    {
      name: "single descending sort without sortIndex",
      state: [{ colId: A, sort: "desc" }, { colId: B, sort: null }],
      sort: [desc(A)], first: R3, next: R2,
    },
  ])("preserves $name", async ({ state, sort, first, next }) => {
    const nav = gridNavQuery({ columnState: state, search: "  " });
    expect(nav.sort).toEqual(sort);
    expect(nav.search).toBeUndefined();
    expect(nav.narrowed).toBe(false);
    const view = await fetchRowInView(DATASET, first, nav);
    expect(view.current.row_id).toBe(first);
    expect(view.next[0]).toBe(next);
  });

  it("accepts absent column state", () => {
    expect(sortFromColumnState()).toEqual([]);
    expect(sortFromColumnState(null)).toEqual([]);
    expect(gridNavQuery().sort).toEqual([]);
  });

  it("preserves prototype multi-sort in physical column order", () => {
    setLaunchOverrideForTest({ launch: false });
    expect(isRealBackend()).toBe(false);
    expect(sortFromColumnState(MULTI_SORT)).toEqual([asc(A), asc(B)]);
    expect(gridNavQuery({ columnState: MULTI_SORT }).sort).toEqual([asc(A), asc(B)]);
    expect(gridNavQuery({ columnState: MULTI_SORT }).useGridNavigation).toBe(false);
  });

  it("retains the full sortIndex-ordered table model and navigation key", () => {
    const nav = gridNavQuery({ columnState: MULTI_SORT });
    expect(nav.sort).toEqual([asc(A)]);
    expect(nav.tableSort).toEqual([asc(B), asc(A)]);
    expect(nav.useGridNavigation).toBe(true);
    const changed = gridNavQuery({ columnState: [
      { colId: A, sort: "asc", sortIndex: 1 },
      { colId: B, sort: "desc", sortIndex: 0 },
    ] });
    expect(changed.sort).toEqual(nav.sort);
    expect(changed.key).not.toBe(nav.key);
  });

  it.each([true, false])("preserves filters and search with launch=%s", (launch) => {
    setLaunchOverrideForTest({ launch });
    const filterConfig = {
      filter_type: "text", filter_op: "equals", filter_value: "one",
    };
    const nav = gridNavQuery({
      columnState: MULTI_SORT,
      filters: [{
        columnId: A,
        filterConfig: { filterType: "text", filterOp: "equals", filterValue: "one" },
      }],
      systemFilters: [{
        column_id: B,
        filter_config: { ...filterConfig, path: "customer.tier" },
      }],
      search: " refund ",
      total: 3,
    });
    expect(nav.filters).toEqual([
      { column_id: A, filter_config: filterConfig },
      { column_id: `${B}::customer.tier`, filter_config: filterConfig },
    ]);
    expect(nav.search).toEqual({ key: "refund", type: ["text", "image", "audio"] });
    expect(nav.narrowed).toBe(true);
    expect(nav.total).toBe(3);
    expect(nav.key).toBe(JSON.stringify([nav.filters, nav.tableSort, "refund"]));
  });
});

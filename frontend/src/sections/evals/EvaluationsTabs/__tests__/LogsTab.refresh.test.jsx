import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { act, render } from "src/utils/test-utils";

const fixture = vi.hoisted(() => ({ props: null, get: vi.fn(), api: {
  isDestroyed: () => false, getDisplayedRowCount: () => 1, hideOverlay: vi.fn(), showNoRowsOverlay: vi.fn(),
  deselectAll: vi.fn(), refreshServerSide: vi.fn(), retryServerSideLoads: vi.fn(),
} }));
vi.mock("ag-grid-react", async () => {
  const { forwardRef, useImperativeHandle } = await import("react");
  return { AgGridReact: forwardRef((props, ref) => {
    fixture.props = props;
    useImperativeHandle(ref, () => ({ api: fixture.api }));
    return <div role="grid" />;
  }) };
});
vi.mock("src/utils/axios", () => ({ default: { get: (...args) => fixture.get(...args) }, endpoints: { develop: { eval: { getEvalsLogs: "/eval-logs/" } } } }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => null }));
vi.mock("src/sections/develop-detail/Common/SingleImageViewer/SingleImageViewerProvider", () => ({ default: ({ children }) => children }));
vi.mock("../../DevelopFilters/DevelopFilterBox", () => ({ default: () => null }));

import LogsTab from "../LogsTab";

const page = (status) => ({ data: { result: {
  column_config: [{ id: "score", name: "Score", data_type: "number", status }],
  table: [{ row_id: "current", log_id: "current" }],
  metadata: { total_rows: 11, total_pages: 2, current_page_index: 1, page_size: 10,
    query_complete: true, query_status: "complete", query_sampled: false },
} } });
const params = () => ({ request: { startRow: 10, endRow: 20,
  sortModel: [{ colId: "score", sort: "desc" }] }, api: fixture.api,
  success: vi.fn(), fail: vi.fn() });
afterEach(() => vi.useRealTimers());

it("refreshes through the grid and skips ticks during an active exact read", async () => {
  vi.useFakeTimers();
  fixture.get.mockReset();
  fixture.api.refreshServerSide.mockReset();
  fixture.get.mockResolvedValueOnce(page("Running"));
  const view = render(<LogsTab evalFilterOpen={false} setEvalFilterOpen={() => {}} />);
  await act(async () => { await fixture.props.serverSideDatasource.getRows(params()); });
  expect(fixture.get.mock.calls[0][1].params).toMatchObject({
    current_page_index: 1, page_size: 10,
    sort: JSON.stringify([{ column_id: "score", type: "descending" }]),
  });
  await act(async () => vi.advanceTimersByTimeAsync(10_000));
  expect(fixture.api.refreshServerSide).toHaveBeenCalledOnce();
  expect(fixture.api.refreshServerSide).toHaveBeenCalledWith({ purge: false });
  expect(fixture.get).toHaveBeenCalledTimes(1); // No separate unsorted refresh HTTP path.

  let finish;
  fixture.get.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const second = params();
  let pending;
  act(() => { pending = fixture.props.serverSideDatasource.getRows(second); });
  await act(async () => vi.advanceTimersByTimeAsync(30_000));
  expect(fixture.api.refreshServerSide).toHaveBeenCalledTimes(1);
  expect(second.success).not.toHaveBeenCalled();
  await act(async () => { finish(page("Complete")); await pending; });
  expect(second.success).toHaveBeenCalledOnce();
  await act(async () => vi.advanceTimersByTimeAsync(20_000));
  expect(fixture.api.refreshServerSide).toHaveBeenCalledTimes(1);
  view.unmount();
});

it("cancels its datasource read on unmount and discards a late response", async () => {
  fixture.get.mockReset();
  let signal;
  let finish;
  fixture.get.mockImplementation((_url, config) => {
    signal = config.signal;
    return new Promise((resolve) => { finish = resolve; });
  });
  const view = render(<LogsTab evalFilterOpen={false} setEvalFilterOpen={() => {}} />);
  const request = params();
  let pending;
  act(() => { pending = fixture.props.serverSideDatasource.getRows(request); });
  view.unmount();
  await act(async () => { await pending; });
  expect(signal.aborted).toBe(true);
  expect(request.success).not.toHaveBeenCalled();
  await act(async () => { finish(page("Complete")); });
  expect(request.success).not.toHaveBeenCalled();
});

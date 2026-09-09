import React from "react";
import { expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "src/utils/test-utils";

const fixture = vi.hoisted(() => ({
  props: null, get: vi.fn(),
  api: { isDestroyed: () => false, hideOverlay: vi.fn(), showNoRowsOverlay: vi.fn(),
    deselectAll: vi.fn(), retryServerSideLoads: vi.fn() },
}));
vi.mock("ag-grid-react", async () => {
  const { forwardRef, useImperativeHandle } = await import("react");
  return { AgGridReact: forwardRef((props, ref) => {
    fixture.props = props;
    useImperativeHandle(ref, () => ({ api: fixture.api }));
    return <div role="grid" />;
  }) };
});
vi.mock("src/utils/axios", () => ({ default: { get: (...args) => fixture.get(...args) }, endpoints: { develop: { eval: { getEvalsLogs: "/eval-logs/" } } } }));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ role: "admin" }) }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => null }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("src/sections/develop-detail/Common/SingleImageViewer/SingleImageViewerProvider", () => ({ default: ({ children }) => children }));
vi.mock("../../../DevelopFilters/DevelopFilterBox", () => ({ default: () => null }));
vi.mock("src/components/ColumnDropdown/ColumnDropdown", () => ({ default: () => null }));
vi.mock("../CellRenderingData", () => ({ CustomCellRender: () => null, CustomDevelopDetailColumn: () => null }));
vi.mock("../LogsDrawer", () => ({ default: () => null }));
vi.mock("../NoResultsUI", () => ({ default: () => null }));
vi.mock("src/components/TableFilterOptions/TableFilterOptions", () => ({ default: () => null }));

import LogsTabGrid from "../LogsTabGrid";

const page = (id) => ({ data: { result: {
  column_config: [], table: [{ row_id: id, log_id: id }],
  metadata: { total_rows: 1, total_pages: 1, current_page_index: 0, page_size: 10,
    query_complete: true, query_status: "complete", query_sampled: false },
} } });
const params = () => ({ request: { startRow: 0, endRow: 10, sortModel: [] },
  api: fixture.api, success: vi.fn(), fail: vi.fn() });

it.each(["date", "template", "feedback", "playground"])("replaces the datasource on %s changes and rejects the old page", async (change) => {
  fixture.get.mockReset();
  const signals = [];
  const completions = [];
  fixture.get.mockImplementation((_url, { signal }) => {
    signals.push(signal);
    return new Promise((resolve) => completions.push(resolve));
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const initialDate = { filter_config: { filter_type: "datetime", filter_op: "between", filter_value: ["2026-09-01", "2026-09-01"] } };
  const nextDate = { filter_config: { ...initialDate.filter_config, filter_value: ["2026-09-02", "2026-09-02"] } };
  const renderGrid = (changed) => <QueryClientProvider client={client}><LogsTabGrid
    templateId={changed && change === "template" ? "eval-2" : "eval-1"}
    dateFilter={changed && change === "date" ? nextDate : initialDate}
    isFeedback={changed && change === "feedback"}
    isEvalPlayGround={changed && change === "playground"}
  /></QueryClientProvider>;
  const view = render(renderGrid(false));
  const firstSource = fixture.props.serverSideDatasource;
  expect(firstSource).toBeDefined();
  expect(fixture.props.onGridReady).toBeUndefined();
  expect(fixture.get).not.toHaveBeenCalled(); // No manual duplicate first-page request.
  const first = params();
  let oldRead;
  act(() => { oldRead = firstSource.getRows(first); });
  view.rerender(renderGrid(true));
  const secondSource = fixture.props.serverSideDatasource;
  expect(secondSource).not.toBe(firstSource);
  await act(async () => { await oldRead; });
  expect(signals[0].aborted).toBe(true);
  expect(first.success).not.toHaveBeenCalled();
  expect(first.fail).toHaveBeenCalledOnce();
  const second = params();
  let newRead;
  act(() => { newRead = secondSource.getRows(second); });
  await act(async () => {
    completions[0](page("obsolete"));
    completions[1](page("current"));
    await newRead;
  });
  expect(first.success).not.toHaveBeenCalled();
  expect(second.success).toHaveBeenCalledWith({ rowData: [expect.objectContaining({ rowId: "current" })], rowCount: 1 });
  expect(second.fail).not.toHaveBeenCalled();
  view.unmount();
  client.clear();
});

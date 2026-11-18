import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";
import { AllEnterpriseModule } from "ag-grid-enterprise";

// Renders the real grid (AG Grid server-side row model) against a scripted
// dataset-detail endpoint: `server.isDone(request, kind)` decides whether the
// upload has finished processing when each request is served, and
// `server.delayMs(request, kind)` how long its response takes.
const { getMock, gridState, server, Null, Passthrough } = vi.hoisted(() => ({
  getMock: vi.fn(),
  gridState: { api: null },
  server: { isDone: () => false, delayMs: () => 0, requests: [] },
  Null: () => null,
  Passthrough: ({ children }) => children,
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => getMock(...args),
    put: vi.fn(() => Promise.resolve({ data: {} })),
  },
  endpoints: {
    develop: {
      getDatasetDetail: (id) => `/datasets/${id}/detail/`,
      updateDataset: (id) => `/datasets/${id}/`,
    },
  },
}));
vi.mock("../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({
    setGridApi: (api) => {
      gridState.api = api;
    },
    setRefetchTable: () => {},
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => undefined }));
vi.mock("../developDataGrid.css", () => ({}));
vi.mock("src/api/develop/develop-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useDevelopDatasetList: () => ({ data: [] }),
}));
vi.mock("src/sections/common/EvaluationDrawer/getEvalsList", () => ({
  useEvalsList: () => ({ data: undefined }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("../common", async (importOriginal) => ({
  ...(await importOriginal()),
  getColumnConfig: ({ eachCol }) => ({
    field: eachCol.id,
    colId: eachCol.id,
    headerName: eachCol.name,
  }),
}));
vi.mock("../DataTabStatusBar", () => ({ default: Null }));
// The real filter box keeps an enabled observer on the page-0 query, so every
// invalidation of that query refetches it.
vi.mock("../DevelopFilters/DevelopFilterBox", async () => {
  const { useDatasetColumnConfig } = await import(
    "src/api/develop/develop-detail"
  );
  return {
    default: function DevelopFilterBox() {
      useDatasetColumnConfig("dataset-1", false, true);
      return null;
    },
  };
});
vi.mock("../TopBanner", () => ({ default: Null }));
vi.mock("../DatapointDrawerV2/DatapointDrawerV2", () => ({ default: Null }));
vi.mock("../AddRowData", () => ({ default: Null }));
vi.mock("../EditColumnName", () => ({ default: Null }));
vi.mock("../EditColumnType", () => ({ default: Null }));
vi.mock("../DeleteColumn", () => ({ default: Null }));
vi.mock("../AddEvaluationFeeback/AddEvaluationFeeback", () => ({
  default: Null,
}));
vi.mock("../ImprovePrompt/ImprovePrompt", () => ({ default: Null }));
vi.mock("../DoubleClickEditCell/DoubleClickEditCell", () => ({
  default: Null,
}));
vi.mock("src/components/PdfPreviewDrawer", () => ({ default: Null }));
vi.mock(
  "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/CompositeEvalDialog",
  () => ({ default: Null }),
);
vi.mock(
  "src/components/custom-audio/context-provider/AudioPlaybackContext",
  () => ({ AudioPlaybackProvider: Passthrough }),
);
vi.mock("../../Common/SingleImageViewer/SingleImageViewerProvider", () => ({
  default: Passthrough,
}));
vi.mock("../../Common/MultiImageViewer", () => ({
  MultiImageViewerProvider: Passthrough,
}));
vi.mock(
  "src/sections/common/DevelopCellRenderer/CellRenderers/RunningSkeletonRenderer",
  () => ({ default: Null }),
);

import DevelopDataV2 from "../DevelopDataV2";
import { DUMMY_ROWS } from "../common";

ModuleRegistry.registerModules([AllCommunityModule, AllEnterpriseModule]);

const COLUMNS = [{ id: "col-a", name: "question", status: "Completed" }];
const ROW_IDS = ["row-1", "row-2", "row-3"];
const PLACEHOLDER_IDS = DUMMY_ROWS.map(({ rowId }) => String(rowId));

const detailResponse = (_url, { params }) => {
  const kind = params.column_config_only ? "columns" : "page";
  const request = server.requests.length;
  const done = server.isDone(request, kind);
  server.requests.push(`${kind}:${done ? "done" : "processing"}`);
  let result;
  if (!done) {
    result = { column_config: [], is_processing_data: true };
    if (kind === "page") {
      result = { ...result, table: [], metadata: { total_rows: 0 } };
    }
  } else if (kind === "columns") {
    result = { column_config: COLUMNS };
  } else {
    result = {
      column_config: COLUMNS,
      table: ROW_IDS.map((row_id) => ({ row_id, "col-a": { cell_value: 1 } })),
      metadata: { total_rows: ROW_IDS.length },
    };
  }
  return new Promise((resolve) => {
    setTimeout(
      () => resolve({ data: { result } }),
      server.delayMs(request, kind),
    );
  });
};

const gridRowIds = () => {
  const ids = [];
  gridState.api.forEachNode((node) =>
    ids.push(String(node.data?.row_id ?? node.data?.rowId)),
  );
  return ids;
};

// Step in small slices so React renders and runs effects between timers,
// as a browser would; one long act() would hold them back until the end.
const advance = async (ms) => {
  for (let elapsed = 0; elapsed < ms; elapsed += 250) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
  }
};

const renderGrid = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, staleTime: 5000 } },
        })
      }
    >
      <MemoryRouter>
        <DevelopDataV2 datasetId="dataset-1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe("DevelopDataV2 while an uploaded file is processing", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    gridState.api = null;
    server.requests = [];
    server.isDone = () => false;
    server.delayMs = () => 0;
    getMock.mockReset();
    getMock.mockImplementation(detailResponse);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("replaces the placeholder rows once processing finishes", async () => {
    let finished = false;
    server.isDone = () => finished;
    renderGrid();
    await advance(1000);
    expect(gridRowIds()).toEqual(PLACEHOLDER_IDS);

    finished = true;
    await advance(30000);

    expect(gridRowIds()).toEqual(ROW_IDS);
  });

  it("replaces them at the flip when processing ends between a poll's page and column reads", async () => {
    // As in the failed e2e run: the first poll's (5s) page reads still see
    // the upload processing and its column-config read sees it done. The
    // filter box's refetch left that processing page in the cache as fresh;
    // the grid refresh fired by the column config must not serve it.
    let columnReads = 0;
    server.isDone = (_request, kind) => {
      if (kind === "columns") columnReads += 1;
      return columnReads >= 2;
    };
    renderGrid();
    await advance(1000);
    expect(gridRowIds()).toEqual(PLACEHOLDER_IDS);

    await advance(8000);

    // Still before the next poll (10s), which would otherwise recover it.
    expect(columnReads).toBe(2);
    expect(gridRowIds()).toEqual(ROW_IDS);
  });

  it("replaces them when a page read from before processing ended lands late", async () => {
    // The first page read (request 0) takes 6s; processing ends before the
    // poll's reads, and the grid refresh fired by the column config joins
    // that still-pending read and gets the placeholders back.
    server.isDone = (request) => request >= 2;
    server.delayMs = (request, kind) =>
      kind === "page" && request === 0 ? 6000 : 50;
    renderGrid();

    await advance(30000);

    expect(gridRowIds()).toEqual(ROW_IDS);
  });
});

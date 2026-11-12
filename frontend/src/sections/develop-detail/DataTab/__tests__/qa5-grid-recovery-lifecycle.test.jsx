import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";
import { AllEnterpriseModule } from "ag-grid-enterprise";

// Exercise the existing public component and actual server-side grid, not an
// export introduced by the repair. This test also runs unchanged on P-prime.
const { net, state, Null, Pass } = vi.hoisted(() => ({
  net: { get: vi.fn() },
  state: { api: null, failure: null },
  Null: () => null,
  Pass: ({ children }) => children,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => net.get(...args), put: vi.fn() },
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
      state.api = api;
    },
    setRefetchTable: () => {},
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => undefined }));
vi.mock("../developDataGrid.css", () => ({}));
vi.mock("src/api/develop/develop-detail", async (original) => ({
  ...(await original()),
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
vi.mock("../common", async (original) => ({
  ...(await original()),
  getColumnConfig: ({ eachCol }) => ({
    field: eachCol.id,
    colId: eachCol.id,
    headerName: eachCol.name,
  }),
}));
vi.mock("../DataTabStatusBar", () => ({ default: Null }));
vi.mock("../DevelopFilters/DevelopFilterBox", () => ({ default: Null }));
vi.mock("../TopBanner", () => ({ default: Null }));
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
  () => ({ AudioPlaybackProvider: Pass }),
);
vi.mock("../../Common/SingleImageViewer/SingleImageViewerProvider", () => ({
  default: Pass,
}));
vi.mock("../../Common/MultiImageViewer", () => ({
  MultiImageViewerProvider: Pass,
}));
vi.mock(
  "src/sections/common/DevelopCellRenderer/CellRenderers/RunningSkeletonRenderer",
  () => ({ default: Null }),
);
import DevelopDataV2 from "../DevelopDataV2";

ModuleRegistry.registerModules([AllCommunityModule, AllEnterpriseModule]);
let client;
beforeEach(() => {
  state.api = null;
  state.failure = null;
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  net.get.mockReset();
  net.get.mockImplementation(async (_url, { params }) => {
    if (!params.column_config_only && state.failure) throw state.failure;
    return {
      data: {
        result: {
          column_config: [
            { id: "col-a", name: "question", status: "Completed" },
          ],
          table: [
            {
              row_id: "minted-row",
              "col-a": { cell_value: "readable fixture" },
            },
          ],
          metadata: { total_rows: 1 },
        },
      },
    };
  });
});
afterEach(() => {
  cleanup();
  client.clear();
});
function mountGrid() {
  return render(
    <QueryClientProvider client={client}>
      <HelmetProvider>
        <MemoryRouter>
          <DevelopDataV2 datasetId="dataset-1" />
        </MemoryRouter>
      </HelmetProvider>
    </QueryClientProvider>,
  );
}

it("replaces failed grid placeholders and retries through to actual rows", async () => {
  state.failure = { statusCode: 500, message: "disposable read failure" };
  mountGrid();
  await waitFor(() => expect(net.get).toHaveBeenCalled(), { timeout: 5000 });
  // Baseline reaches the real failing datasource, then keeps an ERR grid.
  expect(
    await screen.findByRole("alert", {}, { timeout: 5000 }),
  ).toHaveTextContent("Could not load dataset");
  expect(document.querySelector(".ag-root")).toBeNull();
  const failedReads = net.get.mock.calls.filter(
    ([, cfg]) => !cfg.params.column_config_only,
  ).length;
  state.failure = null;
  fireEvent.click(screen.getByRole("button", { name: "Retry", exact: true }));
  await waitFor(
    () => {
      const rows = [];
      state.api?.forEachNode((node) => rows.push(node.data?.row_id));
      expect(rows).toContain("minted-row");
    },
    { timeout: 5000 },
  );
  expect(
    net.get.mock.calls.filter(([, cfg]) => !cfg.params.column_config_only)
      .length,
  ).toBeGreaterThan(failedReads);
  expect(screen.queryByRole("alert")).toBeNull();
});

it.each([403, 404])(
  "replaces a %s table refusal with neutral recovery",
  async (statusCode) => {
    state.failure = { statusCode, message: "unavailable" };
    mountGrid();
    expect(
      await screen.findByText("Dataset not found", {}, { timeout: 5000 }),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Back to Datasets" }),
    ).toBeVisible();
    expect(document.querySelector(".ag-root")).toBeNull();
    expect(document.body.textContent).not.toMatch(
      /foreign|another organization/i,
    );
  },
);

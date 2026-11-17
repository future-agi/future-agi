import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";
import { AllEnterpriseModule } from "ag-grid-enterprise";

// Keep the real grid, popup editor, column value setter and immediate writer.
// Only the transport and unrelated dialogs/renderers are replaced.
const { transport, grid, notice, Null, Passthrough } = vi.hoisted(() => ({
  notice: vi.fn(),
  transport: { get: vi.fn(), post: vi.fn(), value: "Original saved value" },
  grid: { api: null },
  Null: () => null,
  Passthrough: ({ children }) => children,
}));
vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => transport.get(...args),
    post: (...args) => transport.post(...args),
    put: vi.fn(),
  },
  endpoints: {
    develop: {
      getDatasetDetail: (id) => `/datasets/${id}/detail/`,
      updateDataset: (id) => `/datasets/${id}/`,
      updateCellValue: (id) => `/datasets/${id}/update_cell_value/`,
    },
  },
}));
vi.mock("../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({
    setGridApi: (api) => {
      grid.api = api;
    },
    setRefetchTable: () => {},
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({
    can: { view: true, edit: true },
    reason: () => "",
    isLoading: false,
  }),
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
vi.mock("src/api/develop/dataset-splits", () => ({
  useDatasetSplits: () => ({ data: undefined }),
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  enqueueSnackbar: (...args) => notice(...args),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("../common", async (importOriginal) => {
  const original = await importOriginal();
  return {
    ...original,
    getColumnConfig: (params) => ({
      ...original.getColumnConfig(params),
      headerComponent: undefined,
      cellRenderer: undefined,
      headerGroupComponent: undefined,
    }),
  };
});
vi.mock("../DataTabStatusBar", () => ({ default: Null }));
vi.mock("../Splits/ManageSplitsDialog", () => ({ default: Null }));
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
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";
ModuleRegistry.registerModules([AllCommunityModule, AllEnterpriseModule]);
const column = {
  id: "col-a",
  name: "Question",
  data_type: "text",
  origin_type: "dataset",
  status: "Completed",
};
const clients = [];
const clipboardDescriptor = Object.getOwnPropertyDescriptor(
  navigator,
  "clipboard",
);
const setClipboard = (value) =>
  Object.defineProperty(navigator, "clipboard", { configurable: true, value });
const mountGrid = async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 5000 } },
  });
  clients.push(client);
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DevelopDataV2 datasetId="dataset-1" versioned />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(grid.api?.getRowNode("row-1")?.data?.["col-a"]?.cell_value).toBe(
      transport.value,
    ),
  );
  return view;
};
const edit = async (value) => {
  act(() => grid.api.startEditingCell({ rowIndex: 0, colKey: "col-a" }));
  await waitFor(() =>
    expect(document.querySelector(".ag-popup-editor textarea")).not.toBeNull(),
  );
  const input = document.querySelector(".ag-popup-editor textarea");
  input.focus();
  fireEvent.input(input, { target: { value } });
  // AG installs the document popup-dismiss listener on a zero-delay timer.
  // Exercise the established editor, not a key fired before it is attached.
  await settled();
  return input;
};
const settled = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 80));
  });
};

describe("dataset popup cell edit cancellation (GE-01 / FFCORE-01)", () => {
  beforeEach(() => {
    grid.api = null;
    notice.mockClear();
    setRealBackendForTests(true);
    transport.value = "Original saved value";
    transport.get.mockReset().mockImplementation(async () => ({
      data: {
        result: {
          column_config: [column],
          table: [
            { row_id: "row-1", "col-a": { cell_value: transport.value } },
          ],
          metadata: { total_rows: 1 },
          is_processing_data: false,
        },
      },
    }));
    transport.post.mockReset().mockImplementation(async (_url, payload) => {
      transport.value = payload.new_value;
      return { data: { result: {} } };
    });
  });
  afterEach(() => {
    clients.splice(0).forEach((client) => client.clear());
    setRealBackendForTests(null);
    if (clipboardDescriptor)
      Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
    else delete navigator.clipboard;
  });

  const copyAction = (name) => {
    const items = grid.api.getGridOption("getContextMenuItems")({
      api: grid.api,
      column: grid.api.getColumn("col-a"),
      node: grid.api.getRowNode("row-1"),
      value: transport.value,
    });
    return items.find((item) => item.name === name).action;
  };

  it.each(["Copy value", "Copy row link"])(
    "%s waits for clipboard success",
    async (name) => {
      await mountGrid();
      let resolve;
      const writeText = vi.fn(
        () =>
          new Promise((done) => {
            resolve = done;
          }),
      );
      setClipboard({ writeText });
      const pending = copyAction(name)();
      expect(writeText).toHaveBeenCalledWith(
        name === "Copy value"
          ? transport.value
          : `${window.location.origin}/dashboard/develop/dataset-1?tab=data&row=row-1`,
      );
      expect(notice).not.toHaveBeenCalled();
      resolve();
      await pending;
      expect(notice).toHaveBeenCalledWith(
        name === "Copy value" ? "Value copied" : "Row link copied",
        { variant: "success" },
      );
    },
  );

  it.each(["Copy value", "Copy row link"])(
    "%s reports clipboard rejection instead of success",
    async (name) => {
      await mountGrid();
      setClipboard({
        writeText: vi
          .fn()
          .mockRejectedValue(new DOMException("Denied", "NotAllowedError")),
      });
      await copyAction(name)();
      expect(notice).toHaveBeenCalledWith(
        expect.stringMatching(/couldn.t copy/i),
        { variant: "error" },
      );
      expect(
        notice.mock.calls.some(([, options]) => options.variant === "success"),
      ).toBe(false);
    },
  );

  it.each(["Copy value", "Copy row link"])(
    "%s reports an unavailable clipboard",
    async (name) => {
      await mountGrid();
      setClipboard(undefined);
      await copyAction(name)();
      expect(notice).toHaveBeenCalledWith(
        expect.stringMatching(/couldn.t copy/i),
        { variant: "error" },
      );
    },
  );

  it.each([false, true])(
    "Escape cancels before popup close, immediate reload=%s",
    async (reloadImmediately) => {
      const view = await mountGrid();
      const input = await edit("UNSAVED_CANCEL");
      fireEvent.keyDown(input, {
        key: "Escape",
        code: "Escape",
        keyCode: 27,
        bubbles: true,
      });
      if (!reloadImmediately) await settled();
      // This remount uses a new QueryClient and re-reads transport state, not
      // the grid's optimistic row cache. Browser/backend proof is separate.
      view.unmount();
      await settled();
      expect(transport.post).not.toHaveBeenCalled();
      expect(transport.value).toBe("Original saved value");
      await mountGrid();
      expect(grid.api.getRowNode("row-1").data["col-a"].cell_value).toBe(
        "Original saved value",
      );
    },
  );

  it.each(["Tab", "Enter", "blur"])(
    "still commits once on %s",
    async (finish) => {
      await mountGrid();
      const input = await edit("Committed new value");
      if (finish === "blur") {
        const outside = document.createElement("button");
        document.body.appendChild(outside);
        outside.focus();
        outside.remove();
      } else {
        fireEvent.keyDown(input, {
          key: finish,
          code: finish,
          keyCode: finish === "Tab" ? 9 : 13,
          bubbles: true,
        });
      }
      await waitFor(() => expect(transport.value).toBe("Committed new value"));
      await settled();
      expect(transport.post).toHaveBeenCalledTimes(1);
      expect(transport.post).toHaveBeenCalledWith(
        "/datasets/dataset-1/update_cell_value/",
        expect.objectContaining({ new_value: "Committed new value" }),
      );
    },
  );
});

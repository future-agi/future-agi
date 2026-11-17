import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import KnowledgeBaseSheetView from "./knowledge-base-sheet-view";

const harness = vi.hoisted(() => ({
  grid: null,
  api: null,
  post: vi.fn(),
  delete: vi.fn(),
  snackbar: vi.fn(),
  searchParams: new URLSearchParams("tab=name"),
  setSearchParams: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { post: harness.post, delete: harness.delete },
  endpoints: { knowledge: { files: "/knowledge-base/files/" } },
}));
vi.mock("src/utils/constants", () => ({
  APP_CONSTANTS: { AG_GRID_SELECTION_COLUMN: "selection" },
}));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/theme/ag-theme", () => ({ AG_THEME_OVERRIDES: {} }));
vi.mock("src/utils/logger", () => ({ default: { error: vi.fn() } }));
vi.mock("src/utils/utils", () => ({ formatFileSize: (size) => String(size) }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: harness.snackbar }));
vi.mock("react-router", () => ({ useParams: () => ({ knowledgeId: "kb-1" }) }));
vi.mock("react-router-dom", () => ({
  useSearchParams: () => [harness.searchParams, harness.setSearchParams],
}));
vi.mock("@tanstack/react-query", () => ({
  useMutation: ({ mutationFn, onSuccess }) => ({
    isPending: false,
    mutate: async (variables) => {
      const result = await mutationFn(variables);
      onSuccess(result, variables);
    },
  }),
}));
vi.mock("ag-grid-react", async () => {
  const { forwardRef, useImperativeHandle } = await import("react");
  return {
    AgGridReact: forwardRef((props, ref) => {
      harness.grid = props;
      useImperativeHandle(ref, () => ({ api: harness.api }));
      return null;
    }),
  };
});
vi.mock("src/components/show", () => ({
  ShowComponent: ({ condition, children }) => (condition ? children : null),
}));
vi.mock("./nav-section", () => ({ default: () => null }));
vi.mock("./status", () => ({ default: () => null }));
vi.mock("./knowledge-cells", () => ({ TitleCell: () => null }));
vi.mock("../KnowledgeBaseData/CellRenderer", () => ({
  ProcessingStatusCell: () => null,
}));
vi.mock("../CreateKnowledgeBase/CreateKnowledgeBaseDrawer", () => ({
  default: () => null,
}));
vi.mock("./edit-knowledge-base-name-dialog", () => ({ default: () => null }));
vi.mock("src/sections/develop/AddRowDrawer/CreateSyntheticData", () => ({
  default: () => null,
}));
vi.mock("./actions", () => ({
  default: ({ searchQuery, setSearchQuery, setOpenDeleteDialog }) => (
    <>
      <input
        aria-label="Search"
        value={searchQuery}
        onChange={(event) => setSearchQuery(event.target.value)}
      />
      <button onClick={() => setOpenDeleteDialog(true)}>Review deletion</button>
    </>
  ),
}));

const matches = Array.from({ length: 21 }, (_, index) => ({
  id: `policy-${index + 1}`,
  name: `policy-${String(index + 1).padStart(2, "0")}.txt`,
}));
const matchIds = matches.map((file) => file.id);

function listing(rows = matches) {
  return {
    data: {
      result: {
        table_data: rows.slice(0, 10),
        total_rows: rows.length,
        matching_file_ids: rows.map((file) => file.id),
        status: "Completed",
      },
    },
  };
}

async function loadRows(response = listing()) {
  harness.post.mockResolvedValueOnce(response);
  await act(async () => {
    await harness.grid.serverSideDatasource.getRows({
      request: { startRow: 0, endRow: 10, sortModel: [] },
      api: harness.api,
      success: vi.fn(),
      fail: vi.fn(),
    });
  });
}

function selectAll() {
  act(() => {
    harness.grid.onColumnHeaderClicked({
      column: { colId: "selection" },
      api: harness.api,
    });
  });
}

async function confirmDeletion(count) {
  fireEvent.click(screen.getByText("Review deletion"));
  expect(screen.getByText(`Delete ${count} file${count === 1 ? "" : "s"}`)).toBeTruthy();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Delete", exact: true }));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  harness.post.mockReset();
  harness.delete.mockReset();
  harness.delete.mockResolvedValue({});
  harness.api = {
    getCacheBlockState: () => ({}),
    resetColumnState: vi.fn(),
    deselectAll: vi.fn(),
    selectAll: vi.fn(() => {
      harness.grid.onRowSelected({ source: "apiSelectAll" });
    }),
    forEachNode: vi.fn(),
    refreshServerSide: vi.fn(),
    showNoRowsOverlay: vi.fn(),
    hideOverlay: vi.fn(),
  };
  render(<KnowledgeBaseSheetView />);
  fireEvent.change(screen.getByRole("textbox", { name: "Search" }), {
    target: { value: "policy" },
  });
});

afterEach(cleanup);

describe("H10-C25-001 filtered bulk deletion", () => {
  it.each([false, true])(
    "sends only the 21 captured matching IDs, respecting exclusions=%s",
    async (excludeFirst) => {
      await loadRows();
      expect(harness.post).toHaveBeenCalledWith(
        "/knowledge-base/files/",
        expect.objectContaining({ search: "policy", page_size: 10 }),
      );
      selectAll();
      if (excludeFirst) {
        act(() => {
          harness.grid.onRowSelected({
            data: matches[0],
            node: { isSelected: () => false },
          });
        });
      }
      await confirmDeletion(excludeFirst ? 20 : 21);
      expect(harness.delete).toHaveBeenCalledExactlyOnceWith(
        "/knowledge-base/files/",
        { data: { kb_id: "kb-1", delete_all: false, file_ids: excludeFirst ? matchIds.slice(1) : matchIds } },
      );
    },
  );

  it("keeps the confirmation and request bound to the snapshot across refreshes", async () => {
    await loadRows();
    selectAll();
    await loadRows(listing([...matches, { id: "later-match", name: "policy-later.txt" }]));
    await confirmDeletion(21);
    expect(harness.delete.mock.calls[0][1].data).toEqual({
      kb_id: "kb-1", delete_all: false, file_ids: matchIds,
    });
  });

  it("does not fall back to unscoped delete-all when the server omits the snapshot", async () => {
    const response = listing();
    delete response.data.result.matching_file_ids;
    await loadRows(response);
    selectAll();
    await confirmDeletion(0);
    expect(harness.delete).not.toHaveBeenCalled();
  });

  it("clears the captured selection when the search changes", async () => {
    await loadRows();
    selectAll();
    fireEvent.change(screen.getByRole("textbox", { name: "Search" }), {
      target: { value: "unrelated" },
    });
    // The previous query's response must not authorize the new query's select-all.
    selectAll();
    await confirmDeletion(0);
    expect(harness.delete).not.toHaveBeenCalled();
  });

  it("refuses a late response from a previous search", async () => {
    const previousSource = harness.grid.serverSideDatasource;
    fireEvent.change(screen.getByRole("textbox", { name: "Search" }), {
      target: { value: "unrelated" },
    });
    harness.post.mockResolvedValueOnce(listing());
    await act(async () => {
      await previousSource.getRows({
        request: { startRow: 0, endRow: 10, sortModel: [] },
        api: harness.api, success: vi.fn(), fail: vi.fn(),
      });
    });
    selectAll();
    await confirmDeletion(0);
    expect(harness.delete).not.toHaveBeenCalled();
  });

  it("preserves explicit single-file deletion", async () => {
    await loadRows();
    act(() => {
      harness.grid.onRowSelected({
        data: matches[0], node: { isSelected: () => true },
      });
    });
    await confirmDeletion(1);
    expect(harness.delete.mock.calls[0][1].data).toEqual({
      kb_id: "kb-1", delete_all: false, file_ids: [matchIds[0]],
    });
  });
});

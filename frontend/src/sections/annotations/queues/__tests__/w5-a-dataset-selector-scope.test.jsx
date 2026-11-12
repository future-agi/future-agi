import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  userEvent,
} from "src/utils/test-utils";
import AddItemsDialog from "../items/add-items-dialog";

const harness = vi.hoisted(() => ({
  addItems: vi.fn(),
  onClose: vi.fn(),
  fetchQuery: vi.fn(),
  queryClient: null,
  holdDatasetB: false,
  api: null,
  selection: null,
  manualIds: [],
  responseIds: [],
  columns: [{ id: "prompt", name: "Prompt", data_type: "text", is_visible: true }],
}));

vi.mock("src/api/annotation-queues/annotation-queues", async (importOriginal) => ({
  ...(await importOriginal()),
  useAddQueueItems: () => ({ mutate: harness.addItems, isPending: false }),
}));

vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQueryClient: () => harness.queryClient,
  useQuery: ({ queryKey }) => {
    if (queryKey[0] === "datasets-list-simple") {
      return {
        data: [
          { dataset_id: "dataset-a", name: "Dataset A" },
          { dataset_id: "dataset-b", name: "Dataset B" },
        ],
        isLoading: false,
        isFetching: false,
      };
    }
    const loading = queryKey[1] === "dataset-b" && harness.holdDatasetB;
    return {
      data: loading ? undefined : { data: { result: { column_config: harness.columns } } },
      isLoading: loading,
      isFetching: loading,
    };
  },
}));

vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/hooks/use-drawer-stack", () => ({
  useDrawerCovered: () => false,
  coveredDrawerSx: () => ({}),
}));

/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({
  default: ({ src }) => (
    <span data-testid={src.includes("ic_filter.svg") ? "dataset-filter-toggle" : undefined} />
  ),
}));
vi.mock("src/sections/develop-detail/Common/SingleImageViewer/SingleImageViewerProvider", () => ({
  default: ({ children }) => children,
}));

vi.mock("src/components/filter", () => ({
  FilterButton: ({ value, onChange, open, onOpenChange }) => (
    <>
      <button type="button" data-testid="dataset-filter-toggle" onClick={() => onOpenChange(!open)}>Filter</button>
      {open && <input aria-label="Dataset filter value" value={value[0]?.value || ""}
        onChange={(event) => onChange([{
          field: "prompt", fieldType: "string", operator: "contains", value: event.target.value,
        }])}
      />}
    </>
  ),
}));

vi.mock("ag-grid-react", async () => {
  const React = await import("react");
  const AgGridReact = React.forwardRef(function MockDatasetGrid(props, ref) {
    const initialReady = React.useRef(props.onGridReady);
    React.useImperativeHandle(ref, () => ({ api: harness.api }), []);
    React.useEffect(() => {
      initialReady.current({ api: harness.api });
    }, []);
    const emit = () => props.onSelectionChanged({ api: harness.api });
    return (
      <div data-testid="dataset-grid">
        <button type="button" onClick={() => {
          harness.selection = { selectAll: true, toggledNodes: [] };
          emit();
        }}>Select all matching rows</button>
        <button type="button" onClick={() => {
          harness.selection = { selectAll: true, toggledNodes: [harness.responseIds[0]] };
          emit();
        }}>Exclude first row</button>
        <button type="button" onClick={() => {
          harness.selection = { selectAll: false, toggledNodes: [harness.responseIds[0]] };
          harness.manualIds = [harness.responseIds[0]];
          emit();
        }}>Select one row</button>
      </div>
    );
  });
  return { AgGridReact };
});
/* eslint-enable react/prop-types */

const oldIds = Array.from({ length: 12 }, (_, index) =>
  `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
);
const newIds = Array.from({ length: 12 }, (_, index) =>
  `00000000-0000-4000-8000-${String(index + 101).padStart(12, "0")}`,
);

const batchedIds = Array.from({ length: 602 }, (_, index) =>
  `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
);

const enumerationPage = (options) => {
  const page = options.queryKey[2];
  return {
    data: {
      status: true,
      result: {
        column_config: [],
        metadata: { dataset_name: "Scope fixture", total_rows: harness.responseIds.length },
        table: harness.responseIds.slice(page * 10, (page + 1) * 10).map((row_id) => ({ row_id })),
      },
    },
  };
};

beforeEach(() => {
  harness.addItems.mockReset();
  harness.onClose.mockReset();
  harness.fetchQuery.mockReset().mockImplementation(async (options) => enumerationPage(options));
  harness.queryClient = { fetchQuery: harness.fetchQuery };
  harness.holdDatasetB = false;
  harness.selection = { selectAll: false, toggledNodes: [] };
  harness.manualIds = [];
  harness.responseIds = oldIds;
  harness.api = {
    // Datasource replacement deliberately emits no selection callback. The
    // component, not a later AG Grid event, must clear the parent's snapshot.
    setGridOption: vi.fn(),
    getGridOption: vi.fn(() => ({ totalRowCount: 12 })),
    getServerSideSelectionState: vi.fn(() => harness.selection),
    forEachNode: (visit) => harness.manualIds.forEach((row_id) => visit({
      isSelected: () => true,
      data: { row_id },
    })),
    deselectAll: vi.fn(() => {
      harness.selection = { selectAll: false, toggledNodes: [] };
      harness.manualIds = [];
    }),
    getDisplayedRowCount: () => 12,
    getLastDisplayedRowIndex: () => 11,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    isDestroyed: () => false,
  };
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const addButton = () => screen.getByRole("button", { name: /add to queue/i });
const dialog = () => <AddItemsDialog open queueId="queue-scope" onClose={harness.onClose} />;
const searchInput = () => screen.getByPlaceholderText("Search in dataset");

async function chooseDataset(user, name) {
  await user.click(screen.getByRole("combobox", { name: /dataset/i }));
  await user.click(screen.getByRole("option", { name }));
}

function selectRows(mode = "select-all") {
  if (mode === "manual") {
    fireEvent.click(screen.getByRole("button", { name: "Select one row" }));
  } else {
    fireEvent.click(screen.getByRole("button", { name: "Select all matching rows" }));
    fireEvent.click(screen.getByRole("button", { name: "Exclude first row" }));
  }
}

function selectBatchedRows() {
  harness.responseIds = batchedIds;
  harness.api.getGridOption.mockReturnValue({ totalRowCount: batchedIds.length });
  selectRows();
  expect(screen.getByText("601 selected")).toBeInTheDocument();
}

async function completeBatch(index) {
  const [payload, callbacks] = harness.addItems.mock.calls[index];
  await act(async () => {
    callbacks.onSuccess({
      data: { result: { added: payload.items.length, duplicates: 0, errors: [] } },
    });
  });
}

async function openSelectedDialog(mode = "select-all") {
  const user = userEvent.setup();
  const view = render(dialog());
  fireEvent.click(screen.getByText("From Datasets"));
  await chooseDataset(user, "Dataset A");
  fireEvent.change(searchInput(), { target: { value: "alpha" } });
  fireEvent.keyDown(searchInput(), { key: "Enter" });
  selectRows(mode);
  expect(screen.getByText(mode === "manual" ? "1 selected" : "11 selected")).toBeInTheDocument();
  expect(addButton()).toBeEnabled();
  harness.api.deselectAll.mockClear();
  harness.fetchQuery.mockClear();
  return { user, view };
}

function changeFilter(value) {
  if (!screen.queryByLabelText("Dataset filter value")) {
    fireEvent.click(screen.getByTestId("dataset-filter-toggle").closest("button"));
  }
  fireEvent.change(screen.getByLabelText("Dataset filter value"), { target: { value } });
}

async function changeScope(scope, user) {
  if (scope === "dataset") {
    harness.holdDatasetB = true;
    await chooseDataset(user, "Dataset B");
    expect(screen.getByText("Loading dataset...")).toBeInTheDocument();
    expect(screen.queryByTestId("dataset-grid")).not.toBeInTheDocument();
  } else if (scope === "search") {
    fireEvent.change(searchInput(), { target: { value: "beta" } });
  } else {
    changeFilter("beta");
  }
}

function expectCleared() {
  expect(screen.getByText("No rows selected")).toBeInTheDocument();
  expect(addButton()).toBeDisabled();
  expect(harness.api.deselectAll).toHaveBeenCalled();
  expect(harness.selection).toEqual({ selectAll: false, toggledNodes: [] });
  fireEvent.click(addButton());
  expect(harness.fetchQuery).not.toHaveBeenCalled();
  expect(harness.addItems).not.toHaveBeenCalled();
}

describe("W5-A-003 dataset selector scope", () => {
  describe.each(["dataset", "search", "filter"])("%s changes", (scope) => {
    it.each(["select-all", "manual"])("immediately clears %s before another read or submit", async (mode) => {
      const { user } = await openSelectedDialog(mode);
      await changeScope(scope, user);
      expectCleared();
    });

    it("stops old-scope batches when the first of 601 selected rows completes", async () => {
      const { user } = await openSelectedDialog();
      selectBatchedRows();
      await act(async () => { fireEvent.click(addButton()); });
      expect(harness.fetchQuery).toHaveBeenCalledTimes(61);
      expect(harness.addItems).toHaveBeenCalledTimes(1);
      expect(harness.addItems.mock.calls[0][0].items).toEqual(
        batchedIds.slice(1, 501).map((source_id) => ({ source_type: "dataset_row", source_id })),
      );

      // The mutation remains pending while the source/query changes. In the
      // dataset case, keep B's schema loading through the old callback as well.
      await changeScope(scope, user);
      expect(screen.getByText("No rows selected")).toBeInTheDocument();
      expect(addButton()).toBeDisabled();
      await completeBatch(0);

      expect(harness.addItems).toHaveBeenCalledTimes(1);
      expect(harness.onClose).not.toHaveBeenCalled();
      expect(screen.getByText("No rows selected")).toBeInTheDocument();
      expect(addButton()).toBeDisabled();
      if (scope === "dataset") {
        expect(screen.getByRole("combobox", { name: /dataset/i })).toHaveTextContent("Dataset B");
        expect(screen.getByText("Loading dataset...")).toBeInTheDocument();
        expect(screen.queryByTestId("dataset-grid")).not.toBeInTheDocument();
      } else if (scope === "search") {
        expect(searchInput()).toHaveValue("beta");
      } else {
        expect(screen.getByLabelText("Dataset filter value")).toHaveValue("beta");
      }
    });

    it.each(["final batch", "single batch"])("preserves a new selection when the old %s completes", async (interval) => {
      const { user, view } = await openSelectedDialog();
      if (interval === "final batch") selectBatchedRows();
      await act(async () => { fireEvent.click(addButton()); });
      expect(harness.addItems).toHaveBeenCalledTimes(1);
      const pendingIndex = interval === "final batch" ? 1 : 0;
      if (pendingIndex === 1) {
        await completeBatch(0);
        expect(harness.addItems).toHaveBeenCalledTimes(2);
        expect(harness.addItems.mock.calls[1][0].items).toEqual(
          batchedIds.slice(501).map((source_id) => ({ source_type: "dataset_row", source_id })),
        );
      }

      await changeScope(scope, user);
      harness.holdDatasetB = false;
      view.rerender(dialog());
      if (scope === "search") fireEvent.keyDown(searchInput(), { key: "Enter" });
      harness.responseIds = newIds;
      selectRows("manual");
      expect(screen.getByText("1 selected")).toBeInTheDocument();
      await completeBatch(pendingIndex);

      expect(harness.addItems).toHaveBeenCalledTimes(pendingIndex + 1);
      expect(harness.onClose).not.toHaveBeenCalled();
      expect(screen.getByText("1 selected")).toBeInTheDocument();
      expect(addButton()).toBeEnabled();
      expect(screen.getByTestId("dataset-grid")).toBeInTheDocument();
      expect(screen.getByRole("combobox", { name: /dataset/i })).toHaveTextContent(
        scope === "dataset" ? "Dataset B" : "Dataset A",
      );
    });

    it("reselects only the new scope, with its own exclusions and all pages", async () => {
      const { user, view } = await openSelectedDialog();
      await changeScope(scope, user);
      expectCleared();
      harness.holdDatasetB = false;
      view.rerender(dialog());
      if (scope === "search") fireEvent.keyDown(searchInput(), { key: "Enter" });
      harness.responseIds = newIds;
      selectRows();
      expect(screen.getByText("11 selected")).toBeInTheDocument();
      await act(async () => { fireEvent.click(addButton()); });
      expect(harness.fetchQuery).toHaveBeenCalledTimes(2);
      const requests = harness.fetchQuery.mock.calls.map(([options]) => options.queryKey);
      expect(requests.map((key) => key[1])).toEqual([
        scope === "dataset" ? "dataset-b" : "dataset-a",
        scope === "dataset" ? "dataset-b" : "dataset-a",
      ]);
      expect(requests.map((key) => key[2])).toEqual([0, 1]);
      expect(requests.map((key) => key[5])).toEqual([
        scope === "dataset" ? "" : scope === "search" ? "beta" : "alpha",
        scope === "dataset" ? "" : scope === "search" ? "beta" : "alpha",
      ]);
      requests.forEach((key) => {
        if (scope === "filter") {
          expect(key[3]).toEqual([expect.objectContaining({
            column_id: "prompt",
            filter_config: expect.objectContaining({ filter_op: "contains", filter_value: "beta" }),
          })]);
        } else {
          expect(key[3]).toEqual([]);
        }
      });
      expect(harness.addItems).toHaveBeenCalledTimes(1);
      expect(harness.addItems.mock.calls[0][0]).toMatchObject({
        queueId: "queue-scope",
        items: newIds.slice(1).map((source_id) => ({ source_type: "dataset_row", source_id })),
      });
    });
  });

  it("completes all 601 selected rows and closes when the scope stays unchanged", async () => {
    await openSelectedDialog();
    selectBatchedRows();
    await act(async () => { fireEvent.click(addButton()); });
    expect(harness.addItems).toHaveBeenCalledTimes(1);
    await completeBatch(0);
    expect(harness.addItems).toHaveBeenCalledTimes(2);
    expect(harness.addItems.mock.calls.map(([payload]) => payload.items.length)).toEqual([500, 101]);
    expect(harness.addItems.mock.calls.flatMap(([payload]) => payload.items)).toEqual(
      batchedIds.slice(1).map((source_id) => ({ source_type: "dataset_row", source_id })),
    );
    expect(harness.onClose).not.toHaveBeenCalled();
    await completeBatch(1);
    expect(harness.addItems).toHaveBeenCalledTimes(2);
    expect(harness.onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("dataset-grid")).not.toBeInTheDocument();
    expect(screen.getByText("From Datasets")).toBeInTheDocument();
  });

  it("does not accept selection from alpha while beta is still debouncing", async () => {
    await openSelectedDialog();
    vi.useFakeTimers();
    fireEvent.change(searchInput(), { target: { value: "beta" } });
    selectRows();
    expect(screen.getByText("No rows selected")).toBeInTheDocument();
    expect(addButton()).toBeDisabled();
    act(() => { vi.advanceTimersByTime(300); });
    expect(harness.selection).toEqual({ selectAll: false, toggledNodes: [] });
    expect(harness.addItems).not.toHaveBeenCalled();
  });

  it("keeps selection when only normalized search or an incomplete filter is unchanged", async () => {
    await openSelectedDialog();
    fireEvent.change(searchInput(), { target: { value: "  alpha  " } });
    fireEvent.keyDown(searchInput(), { key: "Enter" });
    changeFilter("");
    expect(screen.getByText("11 selected")).toBeInTheDocument();
    expect(addButton()).toBeEnabled();
    expect(harness.api.deselectAll).not.toHaveBeenCalled();
  });

  it("does not post a pending enumeration after the query scope changes", async () => {
    await openSelectedDialog();
    const page = enumerationPage({ queryKey: ["dataset-detail", "dataset-a", 0] });
    let resolvePage;
    harness.fetchQuery.mockImplementationOnce(() => new Promise((resolve) => { resolvePage = resolve; }));
    fireEvent.click(addButton());
    expect(harness.fetchQuery).toHaveBeenCalledTimes(1);
    fireEvent.change(searchInput(), { target: { value: "beta" } });
    expect(screen.getByText("No rows selected")).toBeInTheDocument();
    await act(async () => { resolvePage(page); });
    expect(harness.addItems).not.toHaveBeenCalled();
    expect(addButton()).toBeDisabled();
  });
});

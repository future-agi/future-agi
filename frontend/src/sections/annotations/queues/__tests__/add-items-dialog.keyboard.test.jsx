import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, userEvent } from "src/utils/test-utils";
import AddItemsDialog from "../items/add-items-dialog";

const harness = vi.hoisted(() => ({
  onClose: vi.fn(),
  queryClient: { fetchQuery: vi.fn() },
  api: {
    setGridOption: vi.fn(),
    getGridOption: vi.fn(() => ({ totalRowCount: 0 })),
    getServerSideSelectionState: vi.fn(() => ({
      selectAll: false,
      toggledNodes: [],
    })),
    forEachNode: vi.fn(),
    deselectAll: vi.fn(),
    getDisplayedRowCount: () => 0,
    getLastDisplayedRowIndex: () => -1,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    isDestroyed: () => false,
  },
}));

vi.mock(
  "src/api/annotation-queues/annotation-queues",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useAddQueueItems: () => ({ mutate: vi.fn(), isPending: false }),
  }),
);

vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQueryClient: () => harness.queryClient,
  useQuery: ({ queryKey }) => {
    if (queryKey[0] === "datasets-list-simple") {
      return {
        data: [{ dataset_id: "dataset-a", name: "Dataset A" }],
        isLoading: false,
        isFetching: false,
      };
    }
    return { data: undefined, isLoading: false, isFetching: false };
  },
}));

vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/hooks/use-drawer-stack", () => ({
  useDrawerCovered: () => false,
  coveredDrawerSx: () => ({}),
}));

/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => <span /> }));
vi.mock(
  "src/sections/develop-detail/Common/SingleImageViewer/SingleImageViewerProvider",
  () => ({ default: ({ children }) => children }),
);
vi.mock("src/components/filter", () => ({
  FilterButton: () => <button type="button">Filter</button>,
}));
vi.mock("ag-grid-react", async () => {
  const React = await import("react");
  const AgGridReact = React.forwardRef(function MockDatasetGrid(_props, ref) {
    React.useImperativeHandle(ref, () => ({ api: harness.api }), []);
    return <div data-testid="dataset-grid" />;
  });
  return { AgGridReact };
});
/* eslint-enable react/prop-types */

afterEach(() => {
  cleanup();
  harness.onClose.mockReset();
});

const renderDialog = () =>
  render(
    <AddItemsDialog open queueId="queue-keyboard" onClose={harness.onClose} />,
  );

const datasetsSource = () =>
  screen.getByRole("button", { name: /from datasets/i });

describe("W10-ANNOTATIONS-U06 Add Items keyboard access", () => {
  it("lists every source as a button", () => {
    renderDialog();

    for (const label of [
      /from datasets/i,
      /from traces/i,
      /from spans/i,
      /from sessions/i,
      /from simulation/i,
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeEnabled();
    }
  });

  it("reaches a source with Tab and opens it with Enter", async () => {
    const user = userEvent.setup();
    renderDialog();
    const source = datasetsSource();

    for (let presses = 0; presses < 10; presses += 1) {
      if (document.activeElement === source) break;
      await user.tab();
    }
    expect(source).toHaveFocus();

    await user.keyboard("{Enter}");

    expect(
      screen.getByRole("heading", { name: "Choose from dataset" }),
    ).toBeInTheDocument();
  });

  it("opens a focused source with Space", async () => {
    const user = userEvent.setup();
    renderDialog();
    const source = datasetsSource();

    source.focus();
    expect(source).toHaveFocus();
    await user.keyboard(" ");

    expect(
      screen.getByRole("heading", { name: "Choose from dataset" }),
    ).toBeInTheDocument();
  });

  it("names the close button on the source list", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: "Close" }));

    expect(harness.onClose).toHaveBeenCalledOnce();
  });

  it("names the back and close buttons after a source is picked", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(datasetsSource());
    expect(
      screen.getByRole("heading", { name: "Choose from dataset" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Back to sources" }));

    expect(datasetsSource()).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Choose from dataset" }),
    ).not.toBeInTheDocument();
  });
});

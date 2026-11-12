import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
} from "src/utils/test-utils";
import AddItemsDialog from "../add-items-dialog";
import { getSpanPhysicalRowId } from "src/sections/projects/LLMTracing/spanPhysicalIdentity";

const harness = vi.hoisted(() => ({
  addItems: vi.fn(),
  enqueueSnackbar: vi.fn(),
  onClose: vi.fn(),
  queryClient: {},
  api: null,
  selectedIds: [],
  projects: [
    { id: "project-a", name: "Project A" },
    { id: "project-b", name: "Project B" },
  ],
}));

// The app mounts SnackbarProvider, but test-utils deliberately omits it.
// Notistack's standalone enqueueSnackbar is uninitialized without that provider;
// mock this UI boundary so success handling can reach resetSelection/onClose.
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: harness.enqueueSnackbar,
}));
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useAddQueueItems: () => ({ mutate: harness.addItems, isPending: false }),
}));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQueryClient: () => harness.queryClient,
  useQuery: ({ queryKey }) => {
    let data;
    if (queryKey[0].startsWith("projects-list-all-for-")) {
      data = harness.projects;
    } else if (queryKey[0] === "datasets-list-simple") {
      data = [{ dataset_id: "dataset-b", name: "Dataset B" }];
    } else if (queryKey[0] === "dataset-detail") {
      data = {
        data: {
          result: {
            column_config: [
              {
                id: "prompt",
                name: "Prompt",
                data_type: "text",
                is_visible: true,
              },
            ],
          },
        },
      };
    }
    return { data, isLoading: false, isFetching: false };
  },
}));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: { source: "sdk" } }),
}));
vi.mock("src/hooks/useDashboards", () => ({
  useDashboardFilterValues: () => ({ data: [] }),
}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/hooks/use-drawer-stack", () => ({
  useDrawerCovered: () => false,
  coveredDrawerSx: () => ({}),
}));
vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  default: () => null,
}));

// Keep the real dialog, pickers, selection callbacks and payload construction;
// replace only the grid and external data/UI boundaries.
/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid={icon} />,
}));
vi.mock(
  "src/sections/develop-detail/Common/SingleImageViewer/SingleImageViewerProvider",
  () => ({
    default: ({ children }) => children,
  }),
);
vi.mock("ag-grid-react", async () => {
  const React = await import("react");
  const AgGridReact = React.forwardRef(function MockGrid(props, ref) {
    const onGridReady = React.useRef(props.onGridReady);
    React.useImperativeHandle(ref, () => ({ api: harness.api }), []);
    React.useEffect(() => {
      onGridReady.current({ api: harness.api });
    }, []);
    return (
      <button
        type="button"
        onClick={() => props.onSelectionChanged({ api: harness.api })}
      >
        Select fixture rows
      </button>
    );
  });
  return { AgGridReact };
});
/* eslint-enable react/prop-types */

beforeEach(() => {
  harness.addItems.mockReset().mockImplementation((payload, callbacks) => {
    callbacks.onSuccess({
      data: {
        result: { added: payload.items.length, duplicates: 0, errors: [] },
      },
    });
  });
  harness.enqueueSnackbar.mockReset();
  harness.onClose.mockReset();
  harness.selectedIds = [];
  harness.api = {
    setGridOption: vi.fn(),
    getGridOption: () => ({}),
    getServerSideSelectionState: () => ({
      selectAll: false,
      toggledNodes: harness.selectedIds,
    }),
    forEachNode: (visit) =>
      harness.selectedIds.forEach((row_id) =>
        visit({
          isSelected: () => true,
          data: { row_id },
        }),
      ),
    deselectAll: vi.fn(() => {
      harness.selectedIds = [];
    }),
    getDisplayedRowCount: () => 0,
    getLastDisplayedRowIndex: () => -1,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    isDestroyed: () => false,
  };
});
afterEach(cleanup);

const addButton = () => screen.getByRole("button", { name: /add to queue/i });
const goBack = () =>
  fireEvent.click(
    screen.getByTestId("solar:alt-arrow-left-linear").closest("button"),
  );

async function chooseProject(user, name) {
  const picker = screen.getByRole("combobox", { name: /project/i });
  await user.clear(picker);
  await user.type(picker, name);
  await user.click(screen.getByRole("option", { name }));
}

async function browseTracesAndGoBack(user) {
  render(
    <AddItemsDialog open queueId="queue-scope" onClose={harness.onClose} />,
  );
  fireEvent.click(screen.getByText("From Traces"));
  await chooseProject(user, "Project A");
  goBack();
}

const telemetryCases = [
  { label: "From Spans", sourceType: "observation_span" },
  { label: "From Sessions", sourceType: "trace_session" },
];

function selectTelemetryRows(sourceType, count) {
  const sourceIds = Array.from(
    { length: count },
    (_, index) => `source-b-${index}`,
  );
  harness.selectedIds =
    sourceType === "observation_span"
      ? sourceIds.map((span_id) =>
          getSpanPhysicalRowId({
            project_id: "project-b",
            trace_id: "trace-b",
            span_id,
            start_time: "2026-01-01T00:00:00.000Z",
          }),
        )
      : sourceIds;
  fireEvent.click(screen.getByRole("button", { name: "Select fixture rows" }));
  return sourceIds;
}

describe("W10-ANNOTATIONS-L02 active selector project scope", () => {
  describe.each(telemetryCases)("$label", ({ label, sourceType }) => {
    it.each([1, 501])(
      "submits %i manual rows with project B after returning from trace project A",
      async (count) => {
        const user = userEvent.setup();
        await browseTracesAndGoBack(user);
        fireEvent.click(screen.getByText(label));
        await chooseProject(user, "Project B");
        const sourceIds = selectTelemetryRows(sourceType, count);
        expect(addButton()).toBeEnabled();
        fireEvent.click(addButton());

        await waitFor(() => expect(harness.onClose).toHaveBeenCalledOnce());
        expect(harness.enqueueSnackbar).toHaveBeenCalledExactlyOnceWith(
          `${count} item${count === 1 ? "" : "s"} added to queue`,
          { variant: "success" },
        );
        const payloads = harness.addItems.mock.calls.map(
          ([payload]) => payload,
        );
        expect(payloads).toHaveLength(Math.ceil(count / 500));
        payloads.forEach((payload) => {
          expect(payload).toMatchObject({
            queueId: "queue-scope",
            project_id: "project-b",
          });
        });
        expect(payloads.flatMap((payload) => payload.items)).toEqual(
          sourceIds.map((source_id) => ({
            source_type: sourceType,
            source_id,
          })),
        );
      },
    );

    it("updates the manual scope when the active picker changes projects", async () => {
      const user = userEvent.setup();
      render(
        <AddItemsDialog open queueId="queue-scope" onClose={harness.onClose} />,
      );
      fireEvent.click(screen.getByText(label));
      await chooseProject(user, "Project A");
      await chooseProject(user, "Project B");
      selectTelemetryRows(sourceType, 1);
      fireEvent.click(addButton());

      expect(harness.addItems).toHaveBeenCalledWith(
        expect.objectContaining({ project_id: "project-b" }),
        expect.any(Object),
      );
    });
  });

  it("clears the trace project when returning to unscoped dataset rows", async () => {
    const user = userEvent.setup();
    await browseTracesAndGoBack(user);
    fireEvent.click(screen.getByText("From Datasets"));
    await user.click(screen.getByRole("combobox", { name: /dataset/i }));
    await user.click(screen.getByRole("option", { name: "Dataset B" }));
    harness.selectedIds = ["row-b"];
    fireEvent.click(
      screen.getByRole("button", { name: "Select fixture rows" }),
    );
    fireEvent.click(addButton());

    expect(harness.addItems).toHaveBeenCalledWith(
      {
        queueId: "queue-scope",
        project_id: null,
        items: [{ source_type: "dataset_row", source_id: "row-b" }],
      },
      expect.any(Object),
    );
  });
});

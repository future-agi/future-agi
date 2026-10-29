import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Must be first — polyfills localStorage for Node 23+ before the component
// graph accesses zustand stores at module-init time.
import "./__localstorage_polyfill__.js";

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), get: vi.fn() },
  endpoints: {
    develop: {
      getDatasetColumns: (id) => `/model-hub/datasets/${id}/columns/`,
    },
    project: {
      addExistingDataset: "/tracer/dataset/add_to_existing_dataset/",
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("react-router", async () => {
  const actual = await vi.importActual("react-router");
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useParams: () => ({ observeId: "obs-1" }),
  };
});

// FormSearchSelectFieldState is a complex searchable select.  Replace it with
// a plain <select> so the dataset-picker can be driven in tests.  This is the
// ONLY mock on our own code — every other import is the real module.
vi.mock(
  "src/components/FromSearchSelectField/FormSearchSelectFieldState",
  () => ({
    default: ({ value, onChange, options, label, placeholder }) => {
      const selectable = (options || []).filter(
        (o) => o.value !== "add_column",
      );
      const optRepr = (ov) =>
        typeof ov === "object" && ov !== null
          ? ov.name ?? String(ov)
          : String(ov ?? "");
      const selectedRepr =
        typeof value === "object" && value !== null
          ? value.name ?? ""
          : String(value ?? "");
      return (
        <select
          data-testid={label === "Dataset" ? "dataset-select" : "column-select"}
          aria-label={label}
          value={selectedRepr}
          onChange={(e) => {
            const targetValue = e.target.value;
            const found = selectable.find(
              (o) => optRepr(o.value) === targetValue,
            );
            onChange({ target: { value: found?.value ?? null } });
          }}
        >
          <option value="">{placeholder}</option>
          {selectable.map((o, i) => (
            <option key={i} value={optRepr(o.value)}>
              {o.label}
            </option>
          ))}
        </select>
      );
    },
  }),
);

import axios from "src/utils/axios";
import { enqueueSnackbar as snackbarSpy } from "notistack";
import AddExistingDataset from "../addToDataset/AddExistingDataset";

const POST = axios.post;
const GET = axios.get;

const DEFAULT_PROPS = {
  handleclose: vi.fn(),
  selectedNode: null,
  availableDatasets: [{ name: "Existing DS", dataset_id: "ds-existing" }],
  observationFields: [{ name: "input.text", type: "text" }],
  selectedTraces: ["trace-1"],
  selectedSpans: [],
  selectAll: false,
  currentTab: "trace",
  onSuccess: vi.fn(),
};

function renderComponent(props = {}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <AddExistingDataset {...DEFAULT_PROPS} {...props} />
    </QueryClientProvider>,
  );
}

describe("AddExistingDataset", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  /** Drive the full form flow: render → select dataset → click submit. */
  async function selectDatasetAndSubmit() {
    renderComponent();
    const user = userEvent.setup();

    // Select a dataset — fires the columns query.
    await user.selectOptions(
      screen.getByTestId("dataset-select"),
      "ds-existing",
    );

    // Wait for the columns query to resolve and the mapping to appear.
    // The disabled `name` field is slightly rendered with value "input.text".
    await vi.waitFor(
      () => {
        expect(
          screen.queryAllByDisplayValue("input.text").length,
        ).toBeGreaterThan(0);
      },
      { timeout: 5000 },
    );

    // The "Add to dataset" button should now be enabled and clickable.
    await user.click(screen.getByRole("button", { name: /add to dataset/i }));
  }

  it("shows processing (info) toast when status is not completed", async () => {
    GET.mockResolvedValueOnce({
      data: {
        result: {
          columns: [{ id: "col-1", name: "input.text", data_type: "text" }],
        },
      },
    });
    POST.mockResolvedValueOnce({
      data: {
        status: true,
        result: { status: "processing" },
      },
    });

    await selectDatasetAndSubmit();

    await vi.waitFor(() => {
      expect(snackbarSpy).toHaveBeenCalledTimes(1);
    });
    const [_message, opts] = snackbarSpy.mock.lastCall;
    expect(opts.variant).toBe("info");
  });

  it("shows success toast when status is completed", async () => {
    GET.mockResolvedValueOnce({
      data: {
        result: {
          columns: [{ id: "col-1", name: "input.text", data_type: "text" }],
        },
      },
    });
    POST.mockResolvedValueOnce({
      data: {
        status: true,
        result: { status: "completed" },
      },
    });

    await selectDatasetAndSubmit();

    await vi.waitFor(() => {
      expect(snackbarSpy).toHaveBeenCalledTimes(1);
    });
    const [_message, opts] = snackbarSpy.mock.lastCall;
    expect(opts.variant).toBe("success");
  });

  it("does not show a snackbar when the envelope status is falsy", async () => {
    GET.mockResolvedValueOnce({
      data: {
        result: {
          columns: [{ id: "col-1", name: "input.text", data_type: "text" }],
        },
      },
    });
    POST.mockResolvedValueOnce({
      data: { status: false, result: { status: "processing" } },
    });

    await selectDatasetAndSubmit();

    // Even after waiting, the snackbar should never have been called.
    await vi.waitFor(
      () => {
        expect(POST).toHaveBeenCalled();
      },
      { timeout: 2000 },
    );
    expect(snackbarSpy).not.toHaveBeenCalled();
  });

  it("defaults to processing (info) when result is absent", async () => {
    GET.mockResolvedValueOnce({
      data: {
        result: {
          columns: [{ id: "col-1", name: "input.text", data_type: "text" }],
        },
      },
    });
    POST.mockResolvedValueOnce({
      data: { status: true },
    });

    await selectDatasetAndSubmit();

    await vi.waitFor(() => {
      expect(snackbarSpy).toHaveBeenCalledTimes(1);
    });
    const [_message, opts] = snackbarSpy.mock.lastCall;
    expect(opts.variant).toBe("info");
  });

  it("calls handleclose and onSuccess after mutation succeeds", async () => {
    const handleclose = vi.fn();
    const onSuccess = vi.fn();
    GET.mockResolvedValueOnce({
      data: {
        result: {
          columns: [{ id: "col-1", name: "input.text", data_type: "text" }],
        },
      },
    });
    POST.mockResolvedValueOnce({
      data: {
        status: true,
        result: { status: "processing" },
      },
    });

    const user = userEvent.setup();
    const qc = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    render(
      <QueryClientProvider client={qc}>
        <AddExistingDataset
          {...DEFAULT_PROPS}
          handleclose={handleclose}
          onSuccess={onSuccess}
        />
      </QueryClientProvider>,
    );

    await user.selectOptions(
      screen.getByTestId("dataset-select"),
      "ds-existing",
    );

    await vi.waitFor(
      () => {
        expect(
          screen.queryAllByDisplayValue("input.text").length,
        ).toBeGreaterThan(0);
      },
      { timeout: 5000 },
    );

    await user.click(screen.getByRole("button", { name: /add to dataset/i }));

    await vi.waitFor(() => {
      expect(handleclose).toHaveBeenCalled();
    });
    await vi.waitFor(() => {
      expect(onSuccess).toHaveBeenCalled();
    });
  });

  it("sends the correct POST payload", async () => {
    GET.mockResolvedValueOnce({
      data: {
        result: {
          columns: [{ id: "col-1", name: "input.text", data_type: "text" }],
        },
      },
    });
    POST.mockResolvedValueOnce({
      data: {
        status: true,
        result: { status: "processing" },
      },
    });

    await selectDatasetAndSubmit();

    expect(POST).toHaveBeenCalledTimes(1);
    const [url, payload] = POST.mock.lastCall;
    expect(url).toBe("/tracer/dataset/add_to_existing_dataset/");
    expect(payload).toMatchObject({
      dataset_id: "ds-existing",
      project: "obs-1",
      select_all: false,
      trace_ids: ["trace-1"],
    });
    // The column exists in the dataset → reused column (mapping_config), not new.
    expect(payload.mapping_config).toEqual([
      { col_name: "input.text", data_type: "text", span_field: "input.text" },
    ]);
    expect(payload.new_mapping_config).toEqual([]);
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Must be first — polyfills localStorage for Node 23+ before the component
// graph accesses zustand stores at module-init time.
import "./__localstorage_polyfill__.js";

// Capture the real onSuccess handler so edge cases can be tested without
// relying on React Query's async lifecycle in jsdom.
let capturedOnSuccess = null;

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual("@tanstack/react-query");
  return {
    ...actual,
    useMutation: (options) => {
      capturedOnSuccess = options.onSuccess;
      return actual.useMutation(options);
    },
  };
});

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: {
    project: { addNewDataset: "/tracer/dataset/add_to_new_dataset/" },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("react-router", async () => {
  const actual = await vi.importActual("react-router");
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useParams: () => ({ observeId: "obs-1" }),
  };
});

import axios from "src/utils/axios";
import { enqueueSnackbar as snackbarSpy } from "notistack";
import AddNewDataset from "../addToDataset/AddNewDataset";

const POST = axios.post;

const DEFAULT_PROPS = {
  handleclose: vi.fn(),
  selectedNode: null,
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
      <AddNewDataset {...DEFAULT_PROPS} {...props} />
    </QueryClientProvider>,
  );
}

describe("AddNewDataset", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    capturedOnSuccess = null;
  });

  // ── End-to-end: form submit → POST payload ──────────────────────────────

  it("sends the correct POST payload on submit", async () => {
    POST.mockResolvedValueOnce({
      data: {
        result: { status: "processing", dataset_id: "ds-1" },
      },
    });

    renderComponent();
    const user = userEvent.setup();
    await user.type(
      screen.getByPlaceholderText("Enter new dataset name"),
      "my-dataset",
    );
    await user.click(screen.getByRole("button", { name: /add to dataset/i }));

    await vi.waitFor(() => {
      expect(POST).toHaveBeenCalledTimes(1);
    });

    const [url, payload] = POST.mock.lastCall;
    expect(url).toBe("/tracer/dataset/add_to_new_dataset/");
    expect(payload).toMatchObject({
      new_dataset_name: "my-dataset",
      project: "obs-1",
      select_all: false,
      trace_ids: ["trace-1"],
    });
    expect(payload.mapping_config).toEqual([
      { col_name: "input.text", data_type: "text", span_field: "input.text" },
    ]);
  });

  // ── onSuccess handler: toast variants ────────────────────────────────────

  it("shows info toast when result.status is not 'completed'", () => {
    renderComponent();
    capturedOnSuccess({
      data: { result: { status: "processing", dataset_id: "ds-1" } },
    });

    expect(snackbarSpy).toHaveBeenCalledTimes(1);
    const [, opts] = snackbarSpy.mock.lastCall;
    expect(opts.variant).toBe("info");
  });

  it("shows success toast when result.status is 'completed'", () => {
    renderComponent();
    capturedOnSuccess({
      data: { result: { status: "completed", dataset_id: "ds-1" } },
    });

    expect(snackbarSpy).toHaveBeenCalledTimes(1);
    const [, opts] = snackbarSpy.mock.lastCall;
    expect(opts.variant).toBe("success");
  });

  it("defaults to info when result is absent", () => {
    renderComponent();
    capturedOnSuccess({ data: {} });

    expect(snackbarSpy).toHaveBeenCalledTimes(1);
    const [, opts] = snackbarSpy.mock.lastCall;
    expect(opts.variant).toBe("info");
  });

  it("defaults to info when result.status is null", () => {
    renderComponent();
    capturedOnSuccess({
      data: { result: { status: null, dataset_id: "ds-1" } },
    });

    expect(snackbarSpy).toHaveBeenCalledTimes(1);
    const [, opts] = snackbarSpy.mock.lastCall;
    expect(opts.variant).toBe("info");
  });

  it("defaults to info when result.status is unrecognised", () => {
    renderComponent();
    capturedOnSuccess({
      data: { result: { status: "unknown", dataset_id: "ds-1" } },
    });

    expect(snackbarSpy).toHaveBeenCalledTimes(1);
    const [, opts] = snackbarSpy.mock.lastCall;
    expect(opts.variant).toBe("info");
  });

  it("calls handleclose and onSuccess callback", () => {
    const handleclose = vi.fn();
    const onSuccess = vi.fn();
    render(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: {
              queries: { retry: false },
              mutations: { retry: false },
            },
          })
        }
      >
        <AddNewDataset
          {...DEFAULT_PROPS}
          handleclose={handleclose}
          onSuccess={onSuccess}
        />
      </QueryClientProvider>,
    );

    capturedOnSuccess({
      data: { result: { status: "processing", dataset_id: "ds-2" } },
    });

    expect(handleclose).toHaveBeenCalled();
    expect(onSuccess).toHaveBeenCalled();
  });
});

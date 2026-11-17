import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// GroundTruthDataView serves 1-based pages and clamps page_size to 1..100.
const SERVER_MAX_PAGE_SIZE = 100;
const TOTAL_ROWS = 250;

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  dataset: null,
  evalData: { id: "template-1", config: { required_keys: [] } },
}));

vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgTheme: () => undefined }));
vi.mock("ag-grid-react", () => {
  // Render exactly the rows the tab hands the grid.
  const MockGrid = ({ rowData = [] }) => (
    <ul aria-label="Preview rows">
      {rowData.map((row) => (
        <li key={row.input}>{row.input}</li>
      ))}
    </ul>
  );
  MockGrid.propTypes = { rowData: PropTypes.array };
  return { AgGridReact: MockGrid };
});
vi.mock("react-dropzone", () => ({
  useDropzone: () => ({
    getRootProps: () => ({}),
    getInputProps: () => ({}),
    isDragActive: false,
  }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: [], isLoading: false }),
  useGetDatasetColumns: () => ({ data: [] }),
  useGetDatasetDetail: vi.fn(),
}));
vi.mock("../../hooks/useEvalDetail", () => ({
  useEvalDetail: () => ({ data: mocks.evalData }),
}));
// Keep the real preview query hook; only the transport is mocked.
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => mocks.get(...args) },
  endpoints: {
    develop: {
      eval: {
        groundTruthData: (id) => `/model-hub/ground-truth/${id}/data/`,
      },
    },
  },
}));
vi.mock("../../hooks/useGroundTruth", async (importOriginal) => {
  /** @type {typeof import("../../hooks/useGroundTruth")} */
  const actual = await importOriginal();
  return {
    ...actual,
    useGroundTruthList: () => ({
      data: { items: [mocks.dataset] },
      isLoading: false,
    }),
    useGroundTruthStatus: () => ({ data: undefined }),
    useDeleteGroundTruth: () => ({ isPending: false }),
    useUploadGroundTruth: () => ({ isPending: false }),
    useSaveGroundTruthSetup: () => ({ isPending: false }),
    useTriggerEmbedding: () => ({ isPending: false }),
  };
});

import EvalGroundTruthTab from "../EvalGroundTruthTab";

const makeDataset = (id) => ({
  id,
  name: `Reference answers ${id}`,
  row_count: TOTAL_ROWS,
  columns: ["input"],
  embedding_status: "completed",
  variable_mapping: {},
  role_mapping: { output: "input" },
});

// Mirrors GroundTruthDataView: slice the stored rows for one clamped page.
function serveGroundTruthPage(url, { params }) {
  const match = /\/ground-truth\/([^/]+)\/data\/$/.exec(url);
  if (!match) return Promise.reject(new Error(`Unexpected GET ${url}`));
  const id = match[1];
  const page = Math.max(1, Number(params.page));
  const pageSize = Math.min(
    SERVER_MAX_PAGE_SIZE,
    Math.max(1, Number(params.page_size)),
  );
  const start = (page - 1) * pageSize;
  const rows = Array.from({ length: TOTAL_ROWS }, (_, index) => ({
    input: `${id} row ${index + 1}`,
  })).slice(start, start + pageSize);
  return Promise.resolve({
    data: {
      result: {
        id,
        page,
        page_size: pageSize,
        total_rows: TOTAL_ROWS,
        total_pages: Math.ceil(TOTAL_ROWS / pageSize),
        columns: ["input"],
        rows,
      },
    },
  });
}

let queryClient;

function renderTab() {
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const view = () => (
    <QueryClientProvider client={queryClient}>
      <EvalGroundTruthTab templateId="template-1" />
    </QueryClientProvider>
  );
  return { ...render(view()), view };
}

const requestedParams = () =>
  mocks.get.mock.calls.map(([, config]) => config.params);

describe("Ground truth data preview paging (W10-EVALS-L05)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockImplementation(serveGroundTruthPage);
    mocks.dataset = makeDataset("gt-1");
  });

  afterEach(() => {
    cleanup();
    queryClient?.clear();
  });

  it("asks only for pages the server returns in full", async () => {
    renderTab();

    expect(await screen.findByText("gt-1 row 1")).toBeInTheDocument();
    expect(requestedParams().length).toBeGreaterThan(0);
    for (const params of requestedParams()) {
      expect(params.page_size).toBeLessThanOrEqual(SERVER_MAX_PAGE_SIZE);
    }
  });

  it("pages through to the last row of a dataset larger than one page", async () => {
    renderTab();
    await screen.findByText("gt-1 row 1");

    fireEvent.change(screen.getByRole("combobox", { name: "Rows per page" }), {
      target: { value: "100" },
    });
    await waitFor(() =>
      expect(requestedParams().at(-1)).toEqual({ page: 1, page_size: 100 }),
    );
    expect(await screen.findByText("gt-1 row 100")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("gt-1 row 101")).toBeInTheDocument();
    expect(screen.queryByText("gt-1 row 1")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("gt-1 row 250")).toBeInTheDocument();
    expect(screen.getByText("gt-1 row 201")).toBeInTheDocument();
    expect(requestedParams().at(-1)).toEqual({ page: 3, page_size: 100 });
    expect(screen.getByText("201–250 of 250")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("starts a newly active dataset on its first page", async () => {
    const { rerender, view } = renderTab();
    await screen.findByText("gt-1 row 1");
    const firstPageSize = requestedParams()[0].page_size;

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(
      await screen.findByText(`gt-1 row ${firstPageSize + 1}`),
    ).toBeInTheDocument();

    mocks.dataset = makeDataset("gt-2");
    rerender(view());

    expect(await screen.findByText("gt-2 row 1")).toBeInTheDocument();
    // The new dataset never inherits the previous dataset's page.
    const secondDatasetParams = mocks.get.mock.calls
      .filter(([url]) => url === "/model-hub/ground-truth/gt-2/data/")
      .map(([, config]) => config.params);
    expect(secondDatasetParams).toEqual([
      { page: 1, page_size: firstPageSize },
    ]);
    expect(
      screen.getByRole("button", { name: "Previous page" }),
    ).toBeDisabled();
  });
});

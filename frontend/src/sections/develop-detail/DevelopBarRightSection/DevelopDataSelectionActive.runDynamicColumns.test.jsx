import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "src/utils/axios";
import { palette } from "src/theme/palette";

// The bulk Run dialog on selected dataset rows must offer dynamic (computed)
// columns next to prompt and eval columns, and rerun a selected dynamic
// column through its rerun-operation endpoint.

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  const pending = () => new Promise(() => {});
  return {
    ...actual,
    default: {
      ...actual.default,
      get: (...args) => mockGet(...args),
      post: (...args) => mockPost(...args),
      put: pending,
      delete: pending,
    },
  };
});

// AG Grid does not lay out in jsdom: render each row as a labelled checkbox
// and expose the selected rows the way the real grid api does.
vi.mock("ag-grid-react", async () => {
  const ReactModule = await import("react");
  const AgGridReact = ReactModule.forwardRef(
    function MockAgGridReact(props, ref) {
      const { rowData, onSelectionChanged } = props;
      const selected = ReactModule.useRef(new Set());
      ReactModule.useImperativeHandle(
        ref,
        () => ({
          api: {
            getSelectedRows: () =>
              (rowData || []).filter((row) => selected.current.has(row.field)),
          },
        }),
        [rowData],
      );
      return (
        <div>
          {(rowData || []).map((row) => (
            <label key={row.field}>
              <input
                type="checkbox"
                onChange={(event) => {
                  if (event.target.checked) {
                    selected.current.add(row.field);
                  } else {
                    selected.current.delete(row.field);
                  }
                  onSelectionChanged?.();
                }}
              />
              {row.content}
            </label>
          ))}
        </div>
      );
    },
  );
  AgGridReact.propTypes = {
    rowData: PropTypes.array,
    onSelectionChanged: PropTypes.func,
  };
  return { AgGridReact };
});

vi.mock("src/hooks/use-ag-theme", async (importOriginal) => ({
  ...(await importOriginal()),
  useAgThemeWithoutGridWith: () => ({}),
}));

vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    enqueueSnackbar: () => {},
    useSnackbar: () => ({
      enqueueSnackbar: () => {},
      closeSnackbar: () => {},
    }),
  };
});

vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: () => {},
}));

vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: "Owner", user: {} }),
}));

vi.mock(
  "src/sections/develop-detail/Context/DevelopDetailContext",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useDevelopDetailContext: () => ({
      refreshGrid: () => {},
      gridApi: { current: null },
    }),
  }),
);

vi.mock("src/sections/develop-detail/states", async (importOriginal) => ({
  ...(await importOriginal()),
  useDevelopSelectedRowsStoreShallow: (select) =>
    select({
      selectAll: false,
      toggledNodes: ["row-1"],
      resetSelectedRows: () => {},
    }),
}));

const DATASET_ID = "dataset-1";

const EVAL = { id: "eval-1", name: "Toxicity" };
const PROMPT_COLUMN = {
  id: "col-prompt",
  name: "Summary prompt",
  origin_type: "run_prompt",
  source_id: "prompt-1",
};
const STATIC_COLUMN = { id: "col-input", name: "Input", origin_type: "OTHERS" };
const UNTYPED_COLUMN = { id: "col-untyped", name: "Untyped column" };

const theme = createTheme({ palette: palette("light") });

function mockDataset(columnConfig) {
  mockGet.mockImplementation((url) => {
    if (url === endpoints.develop.eval.getEvalsList(DATASET_ID)) {
      return Promise.resolve({ data: { result: { evals: [EVAL] } } });
    }
    if (url === endpoints.develop.getDatasetDetail(DATASET_ID)) {
      return Promise.resolve({
        data: { result: { column_config: columnConfig } },
      });
    }
    return new Promise(() => {});
  });
}

async function openRunDialog() {
  const { default: DevelopDataSelectionActive } = await import(
    "./DevelopDataSelectionActive"
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={[`/develop/${DATASET_ID}`]}>
          <Routes>
            <Route
              path="/develop/:dataset"
              element={<DevelopDataSelectionActive />}
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Run" }));
  return screen.findByRole("dialog");
}

async function selectAndRun(dialog, names) {
  for (const name of names) {
    fireEvent.click(await within(dialog).findByRole("checkbox", { name }));
  }
  fireEvent.click(within(dialog).getByRole("button", { name: "Run" }));
}

describe("Run on selected dataset rows: dynamic columns", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockPost.mockResolvedValue({ data: {} });
  });

  it("runs a dynamic column alongside eval and prompt columns", async () => {
    mockDataset([
      PROMPT_COLUMN,
      { id: "col-sentiment", name: "Sentiment", origin_type: "classification" },
      STATIC_COLUMN,
      UNTYPED_COLUMN,
    ]);

    const dialog = await openRunDialog();
    await selectAndRun(dialog, ["Toxicity", "Summary prompt", "Sentiment"]);

    expect(
      within(dialog).queryByRole("checkbox", { name: "Input" }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByRole("checkbox", { name: "Untyped column" }),
    ).not.toBeInTheDocument();

    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(3));
    expect(mockPost.mock.calls).toEqual([
      [
        endpoints.develop.evaluateRows(),
        {
          user_eval_metric_ids: ["eval-1"],
          row_ids: ["row-1"],
          selected_all_rows: false,
        },
      ],
      [
        endpoints.develop.evaluateRunRows(),
        {
          run_prompt_ids: ["prompt-1"],
          row_ids: ["row-1"],
          selected_all_rows: false,
        },
      ],
      [
        endpoints.develop.addColumns.updateDynamicColumn("col-sentiment"),
        { operation_type: "classify" },
      ],
    ]);
  });

  it.each([
    ["python_code", "execute_code"],
    ["classification", "classify"],
    ["extracted_entities", "extract_entities"],
    ["extracted_json", "extract_json"],
    ["conditional", "conditional"],
    ["vector_db", "vector_db"],
    ["api_call", "api_call"],
  ])(
    "reruns a %s column with operation_type %s",
    async (originType, operationType) => {
      mockDataset([
        { id: "col-dynamic", name: "Computed", origin_type: originType },
      ]);

      const dialog = await openRunDialog();
      await selectAndRun(dialog, ["Computed"]);

      await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
      expect(mockPost).toHaveBeenCalledWith(
        endpoints.develop.addColumns.updateDynamicColumn("col-dynamic"),
        { operation_type: operationType },
      );
    },
  );
});

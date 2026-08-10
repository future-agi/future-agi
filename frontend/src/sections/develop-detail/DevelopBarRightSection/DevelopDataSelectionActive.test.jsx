import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, userEvent } from "src/utils/test-utils";
import DevelopDataSelectionActive from "./DevelopDataSelectionActive";
import { useDevelopSelectedRowsStore } from "../states";

const mocks = vi.hoisted(() => ({
  axiosGet: vi.fn(),
  axiosPost: vi.fn(),
  axiosDelete: vi.fn(),
  navigate: vi.fn(),
  refreshGrid: vi.fn(),
  deselectAll: vi.fn(),
  enqueueSnackbar: vi.fn(),
  trackEvent: vi.fn(),
}));

vi.mock("react-router", async () => {
  const actual = await vi.importActual("react-router");
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
    useParams: () => ({ dataset: "dataset-1" }),
  };
});

vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    ...actual,
    default: {
      get: mocks.axiosGet,
      post: mocks.axiosPost,
      delete: mocks.axiosDelete,
    },
  };
});

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: {
    rowEvaluationsClicked: "rowEvaluationsClicked",
    rowEvaluationsRunSuccessful: "rowEvaluationsRunSuccessful",
    deleteRowClicked: "deleteRowClicked",
    deleteRowSuccessful: "deleteRowSuccessful",
    duplicateRowClicked: "duplicateRowClicked",
    duplicateRowSuccessful: "duplicateRowSuccessful",
    addRowToNewDatasetSuccessful: "addRowToNewDatasetSuccessful",
  },
  PropertyName: {
    rowEval: "rowEval",
    deleteRow: "deleteRow",
    duplicateRow: "duplicateRow",
    rowToNewDataset: "rowToNewDataset",
  },
  trackEvent: mocks.trackEvent,
}));

vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: mocks.enqueueSnackbar,
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/components/svg-color", () => ({
  default: ({ src, ...props }) => (
    <span data-testid="svg-color" data-src={src} {...props} />
  ),
}));

vi.mock("../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({
    refreshGrid: mocks.refreshGrid,
    gridApi: {
      current: {
        deselectAll: mocks.deselectAll,
        getGridOption: () => ({ totalRowCount: 2 }),
      },
    },
  }),
}));

vi.mock("../DataTab/DataMenuList/DataMenuList", () => ({
  default: () => null,
}));

vi.mock("../DataTab/Duplicate/DuplicateRowAction", () => ({
  default: () => null,
}));

vi.mock("../DataTab/Delete/DeleteRowAction", () => ({
  default: () => null,
}));

vi.mock("../Common/SnackbarWithAction", () => ({
  default: ({ message }) => <span>{message}</span>,
}));

vi.mock("ag-grid-react", async () => {
  const ReactActual = await vi.importActual("react");

  return {
    AgGridReact: ReactActual.forwardRef(
      ({ rowData = [], onSelectionChanged }, ref) => {
        const [selectedRows, setSelectedRows] = ReactActual.useState([]);

        ReactActual.useImperativeHandle(
          ref,
          () => ({
            api: {
              getSelectedRows: () => selectedRows,
            },
          }),
          [selectedRows],
        );

        const toggleRow = (row, checked) => {
          setSelectedRows((current) => {
            const next = checked
              ? [...current, row]
              : current.filter((item) => item.field !== row.field);

            queueMicrotask(() => onSelectionChanged?.());
            return next;
          });
        };

        return (
          <div data-testid="run-evals-grid">
            {rowData.map((row) => (
              <label key={`${row.originType}-${row.field}`}>
                <input
                  type="checkbox"
                  aria-label={`Select ${row.content}`}
                  onChange={(event) => toggleRow(row, event.target.checked)}
                />
                {row.content}
              </label>
            ))}
          </div>
        );
      },
    ),
  };
});

const renderWithQueryClient = (ui) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
};

describe("DevelopDataSelectionActive bulk run", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useDevelopSelectedRowsStore.setState({
      toggledNodes: ["row-1", "row-2"],
      selectAll: false,
    });

    mocks.axiosGet.mockImplementation((url) => {
      if (url.includes("/get_evals_list/")) {
        return Promise.resolve({
          data: {
            result: {
              evals: [
                {
                  id: "eval-1",
                  name: "Quality eval",
                  evalTemplateName: "Quality eval",
                },
              ],
            },
          },
        });
      }

      if (url.includes("/get-dataset-table/")) {
        return Promise.resolve({
          data: {
            result: {
              column_config: [
                {
                  id: "static-col",
                  name: "Question",
                  origin_type: "dataset",
                },
                {
                  id: "entities-col",
                  name: "Entities",
                  origin_type: "extracted_entities",
                },
              ],
            },
          },
        });
      }

      return Promise.resolve({ data: { result: {} } });
    });

    mocks.axiosPost.mockResolvedValue({ data: { result: {} } });
  });

  it("runs selected dynamic columns through the bulk row run action", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<DevelopDataSelectionActive />);

    await user.click(screen.getByRole("button", { name: /^run$/i }));

    await screen.findByText("Entities");
    await user.click(screen.getByLabelText("Select Entities"));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^run$/i })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: /^run$/i }));

    await waitFor(() => {
      expect(mocks.axiosPost).toHaveBeenCalledWith(
        "/model-hub/columns/entities-col/rerun-operation/",
        { operation_type: "extract_entities" },
      );
    });

    expect(mocks.axiosPost).not.toHaveBeenCalledWith(
      "/model-hub/evaluate-rows/",
      expect.anything(),
    );
    expect(mocks.axiosPost).not.toHaveBeenCalledWith(
      "/model-hub/run-prompt-for-rows/",
      expect.anything(),
    );
  });
});

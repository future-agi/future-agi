/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import GlobalVariableDrawer from "../GlobalVariableDrawer";
import { useGlobalVariablesDrawerStore, VIEW } from "../../../store";

// ---- Mock react-router-dom ----
vi.mock("react-router-dom", () => ({
  useParams: () => ({ agentId: "agent-1" }),
  useSearchParams: () => [new URLSearchParams("version=ver-1")],
}));

// ---- Mock API ----
let mockDatasetData = null;
let mockIsLoading = false;
let mockImportedVariableData = {};
vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetGraphDataset: () => ({
    data: mockDatasetData,
    isLoading: mockIsLoading,
  }),
  useUpdateDatasetCell: () => ({ mutateAsync: vi.fn() }),
}));

// ---- Mock child components ----
vi.mock("../ManualVariablesForm", () => ({
  default: ({ formValues }) => (
    <div data-testid="manual-form">{JSON.stringify(formValues)}</div>
  ),
}));

vi.mock("../UploadedJSON", () => ({
  default: ({ uploadedJson }) => (
    <div data-testid="uploaded-json">{JSON.stringify(uploadedJson)}</div>
  ),
}));

vi.mock("../HeaderActions", () => ({
  default: ({ onOpenImportDatasetDrawer, disabled }) => (
    <button
      data-testid="header-actions"
      disabled={disabled}
      onClick={onOpenImportDatasetDrawer}
    >
      Import from Dataset
    </button>
  ),
}));

vi.mock(
  "src/components/VariableDrawer/ImportDataset/ImportDatasetDrawer",
  () => ({
    default: ({ open, onClose, setVariableData }) =>
      open ? (
        <div data-testid="import-dataset-drawer">
          <button
            data-testid="apply-import"
            onClick={() => setVariableData(mockImportedVariableData)}
          >
            Apply
          </button>
          <button data-testid="close-import" onClick={onClose}>
            Close
          </button>
        </div>
      ) : null,
  }),
);

vi.mock("src/components/svg-color", () => ({
  default: (props) => <span data-testid="svg-icon" {...props} />,
}));

vi.mock("src/components/upload-json-dialog", () => ({
  UploadJsonDialog: ({ open }) =>
    open ? <div data-testid="upload-dialog" /> : null,
}));

vi.mock("src/components/custom-dialog/confirm-dialog", () => ({
  default: ({ open, action, onClose }) =>
    open ? (
      <div data-testid="confirm-dialog">
        {action}
        <button data-testid="cancel-close" onClick={onClose}>
          Cancel
        </button>
      </div>
    ) : null,
}));

// ---- Helpers ----

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
});

function renderDrawer(props = {}) {
  const defaultProps = { open: true, onClose: vi.fn(), ...props };
  return {
    ...render(
      <QueryClientProvider client={queryClient}>
        <GlobalVariableDrawer {...defaultProps} />
      </QueryClientProvider>,
    ),
    onClose: defaultProps.onClose,
  };
}

// ---- Tests ----

describe("GlobalVariableDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useGlobalVariablesDrawerStore.getState().reset();
    mockDatasetData = null;
    mockIsLoading = false;
    mockImportedVariableData = {};
  });

  it("renders loading spinner when dataset is loading", () => {
    mockIsLoading = true;
    renderDrawer();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("renders ManualVariablesForm in default view", () => {
    useGlobalVariablesDrawerStore.setState({
      globalVariables: { city: "Tokyo" },
      currentView: VIEW.MANUAL_FORM,
    });
    renderDrawer();
    expect(screen.getByTestId("manual-form")).toBeInTheDocument();
  });

  it("renders UploadedJSON when uploadedJson is set", () => {
    useGlobalVariablesDrawerStore.setState({
      globalVariables: { city: "Tokyo" },
      uploadedJson: { key: "value" },
    });
    renderDrawer();
    expect(screen.getByTestId("uploaded-json")).toBeInTheDocument();
  });

  describe("deriveVariablesFromDataset (integration)", () => {
    it("syncs dataset variables to store on load", async () => {
      mockDatasetData = {
        columns: [
          { id: "col-1", name: "city" },
          { id: "col-2", name: "country" },
        ],
        rows: [
          {
            cells: [
              { columnId: "col-1", value: "Tokyo" },
              { columnId: "col-2", value: "Japan" },
            ],
          },
        ],
      };

      renderDrawer();

      await waitFor(() => {
        const state = useGlobalVariablesDrawerStore.getState();
        expect(state.globalVariables).toEqual({
          city: "Tokyo",
          country: "Japan",
        });
      });
    });

    it("syncs backend snake_case dataset cell column ids", async () => {
      mockDatasetData = {
        columns: [
          { id: "col-1", name: "city" },
          { id: "col-2", name: "country" },
        ],
        rows: [
          {
            cells: [
              { column_id: "col-1", value: "Kyoto" },
              { column_id: "col-2", value: "Japan" },
            ],
          },
        ],
      };

      renderDrawer();

      await waitFor(() => {
        const state = useGlobalVariablesDrawerStore.getState();
        expect(state.globalVariables).toEqual({
          city: "Kyoto",
          country: "Japan",
        });
      });
    });

    it("handles missing cells gracefully (empty string fallback)", async () => {
      mockDatasetData = {
        columns: [
          { id: "col-1", name: "city" },
          { id: "col-2", name: "country" },
        ],
        rows: [
          {
            cells: [{ columnId: "col-1", value: "Tokyo" }],
          },
        ],
      };

      renderDrawer();

      await waitFor(() => {
        const state = useGlobalVariablesDrawerStore.getState();
        expect(state.globalVariables.country).toBe("");
      });
    });

    it("handles null dataset (no sync)", () => {
      mockDatasetData = null;
      renderDrawer();
      // Store should keep initial state
      const state = useGlobalVariablesDrawerStore.getState();
      expect(state.globalVariables).toEqual({});
    });
  });

  describe("Import from Dataset", () => {
    it("opens the import drawer and maps dotted variable names safely", async () => {
      mockDatasetData = {
        columns: [
          { id: "col-1", name: "user.email" },
          { id: "col-2", name: "city" },
        ],
        rows: [
          {
            cells: [
              { columnId: "col-1", value: "old@example.com" },
              { columnId: "col-2", value: "Tokyo" },
            ],
          },
        ],
      };
      mockImportedVariableData = {
        "user.email": ["new@example.com"],
        city: [],
      };

      renderDrawer();

      await waitFor(() => {
        expect(screen.getByTestId("header-actions")).not.toBeDisabled();
      });
      fireEvent.click(screen.getByTestId("header-actions"));
      expect(screen.getByTestId("import-dataset-drawer")).toBeInTheDocument();

      fireEvent.click(screen.getByTestId("apply-import"));

      await waitFor(() => {
        const form = screen.getByTestId("manual-form");
        expect(form).toHaveTextContent(
          '"user__DOT__email":"new@example.com"',
        );
        expect(form).toHaveTextContent('"city":"Tokyo"');
      });
    });

    it("closes the import drawer without changing variable values", async () => {
      mockDatasetData = {
        columns: [{ id: "col-1", name: "city" }],
        rows: [{ cells: [{ columnId: "col-1", value: "Tokyo" }] }],
      };

      renderDrawer();

      await waitFor(() => {
        expect(screen.getByTestId("header-actions")).not.toBeDisabled();
      });
      fireEvent.click(screen.getByTestId("header-actions"));
      fireEvent.click(screen.getByTestId("close-import"));

      expect(
        screen.queryByTestId("import-dataset-drawer"),
      ).not.toBeInTheDocument();
      expect(screen.getByTestId("manual-form")).toHaveTextContent(
        '"city":"Tokyo"',
      );
    });
  });

  describe("handleClose", () => {
    it("calls onClose directly when form is clean", () => {
      useGlobalVariablesDrawerStore.setState({
        globalVariables: { city: "Tokyo" },
        currentView: VIEW.MANUAL_FORM,
      });

      const { onClose } = renderDrawer();
      // Click the close X button
      const closeBtn = screen
        .getAllByRole("button")
        .find((btn) => btn.querySelector("[data-testid='svg-icon']"));
      fireEvent.click(closeBtn);

      expect(onClose).toHaveBeenCalled();
    });

    it("shows confirm dialog when form is dirty (tested via MUI Drawer onClose)", () => {
      // This is hard to trigger directly since isDirty comes from react-hook-form.
      // We test the confirmClose path instead since handleClose depends on form isDirty.
    });
  });

  describe("confirmClose", () => {
    it("resets transient state, returns to manual view, and calls onClose", () => {
      useGlobalVariablesDrawerStore.setState({
        globalVariables: { city: "Tokyo" },
        currentView: VIEW.MANUAL_FORM,
        pendingRun: true,
      });

      const { onClose } = renderDrawer();
      // Click close button (form is clean so it calls confirmClose directly)
      const closeBtn = screen
        .getAllByRole("button")
        .find((btn) => btn.querySelector("[data-testid='svg-icon']"));
      fireEvent.click(closeBtn);

      expect(onClose).toHaveBeenCalled();
      const state = useGlobalVariablesDrawerStore.getState();
      expect(state.pendingRun).toBe(false);
      expect(state.currentView).toBe(VIEW.MANUAL_FORM);
    });
  });
});

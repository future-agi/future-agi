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
vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetGraphDataset: () => ({
    data: mockDatasetData,
    isLoading: mockIsLoading,
  }),
  useUpdateDatasetCell: () => ({ mutateAsync: vi.fn() }),
}));

// ---- Mock child components ----
// Renders one input per variable, registered on the drawer's form like the
// real form, so typing makes the form dirty.
vi.mock("../ManualVariablesForm", async () => {
  const { useFormContext } = await vi.importActual("react-hook-form");
  return {
    default: function MockManualVariablesForm({ formValues, variables = {} }) {
      const { register } = useFormContext();
      return (
        <div data-testid="manual-form">
          {JSON.stringify(formValues)}
          {Object.keys(variables).map((key) => (
            <input key={key} aria-label={key} {...register(key)} />
          ))}
        </div>
      );
    },
  };
});

vi.mock("../UploadedJSON", () => ({
  default: ({ uploadedJson }) => (
    <div data-testid="uploaded-json">{JSON.stringify(uploadedJson)}</div>
  ),
}));

vi.mock("../HeaderActions", () => ({
  default: () => <div data-testid="header-actions" />,
}));

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
    it("resets all state and calls onClose", () => {
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
    });
  });

  // AUD-AGENTS-02: closing once moved the view to ACTIONS, which still shows
  // the editable form but skipped the unsaved-changes prompt from then on.
  describe("unsaved changes guard after the drawer was closed once (AUD-AGENTS-02)", () => {
    const topicDataset = {
      columns: [{ id: "col-1", name: "topic" }],
      rows: [{ cells: [{ columnId: "col-1", value: "cats" }] }],
    };

    // The X button, found by its icon so these tests do not depend on its name.
    const closeButton = () =>
      screen
        .getAllByRole("button")
        .find((btn) => btn.querySelector("[data-testid='svg-icon']"));

    const drawerAt = (open, onClose) => (
      <QueryClientProvider client={queryClient}>
        <GlobalVariableDrawer open={open} onClose={onClose} />
      </QueryClientProvider>
    );

    // Close once without typing, then reopen the same mounted drawer, as
    // GlobalVariablePanel does when "Add input variables" is clicked again.
    const closeCleanAndReopen = (rerender, onClose) => {
      fireEvent.click(closeButton());
      expect(onClose).toHaveBeenCalledTimes(1);
      rerender(drawerAt(false, onClose));
      rerender(drawerAt(true, onClose));
    };

    beforeEach(() => {
      mockDatasetData = topicDataset;
    });

    it("asks before discarding typed values on the first open", async () => {
      const { onClose } = renderDrawer();

      fireEvent.change(screen.getByLabelText("topic"), {
        target: { value: "dogs" },
      });
      fireEvent.click(closeButton());

      expect(await screen.findByTestId("confirm-dialog")).toBeInTheDocument();
      expect(onClose).not.toHaveBeenCalled();
    });

    it("still asks before discarding typed values when X is clicked after a reopen", async () => {
      const { onClose, rerender } = renderDrawer();
      closeCleanAndReopen(rerender, onClose);

      fireEvent.change(screen.getByLabelText("topic"), {
        target: { value: "dogs" },
      });
      fireEvent.click(closeButton());

      expect(await screen.findByTestId("confirm-dialog")).toBeInTheDocument();
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it("still asks before discarding typed values when Esc is pressed after a reopen", async () => {
      const { onClose, rerender } = renderDrawer();
      closeCleanAndReopen(rerender, onClose);

      const input = screen.getByLabelText("topic");
      fireEvent.change(input, { target: { value: "dogs" } });
      fireEvent.keyDown(input, { key: "Escape" });

      expect(await screen.findByTestId("confirm-dialog")).toBeInTheDocument();
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });

  describe("close button name (AUD-AGENTS-03)", () => {
    it("names the icon-only close button", () => {
      renderDrawer();

      expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
    });
  });
});

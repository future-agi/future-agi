/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";
import ManualVariablesForm from "../ManualVariablesForm";
import { useGlobalVariablesDrawerStore } from "../../../store";

// ---- Mocks ----

const mockUpdateCell = vi.fn();
vi.mock("src/api/agent-playground/agent-playground", () => ({
  useUpdateDatasetCell: () => ({ mutateAsync: mockUpdateCell }),
}));

const mockRunWorkflow = vi.fn();
vi.mock("../../../hooks/useWorkflowExecution", () => ({
  default: () => ({ runWorkflow: mockRunWorkflow }),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/components/VariableDrawer/EmptyVariable", () => ({
  default: () => <div data-testid="empty-variable" />,
}));

// ---- Helpers ----

/** Wrapper that provides react-hook-form context with given defaults */
function FormWrapper({ children, defaultValues }) {
  const methods = useForm({ defaultValues });
  return <FormProvider {...methods}>{children}</FormProvider>;
}

function renderForm(props = {}, storeOverrides = {}) {
  const defaults = {
    formValues: { city: "Tokyo", country: "Japan" },
    isDirty: true,
    cellMap: {
      city: { id: "cell-1", columnId: "col-1", value: "Tokyo" },
      country: { id: "cell-2", columnId: "col-2", value: "Japan" },
    },
    graphId: "graph-1",
    variables: { city: "Tokyo", country: "Japan" },
    ...props,
  };

  // Set store state
  useGlobalVariablesDrawerStore.setState({
    globalVariables: { city: "Tokyo", country: "Japan" },
    pendingRun: false,
    ...storeOverrides,
  });

  return render(
    <FormWrapper defaultValues={defaults.formValues}>
      <ManualVariablesForm {...defaults} />
    </FormWrapper>,
  );
}

// ---- Tests ----

describe("ManualVariablesForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useGlobalVariablesDrawerStore.getState().reset();
    mockUpdateCell.mockResolvedValue({});
  });

  it("renders empty state when variables is empty", () => {
    renderForm({ variables: {} });
    expect(screen.getByTestId("empty-variable")).toBeInTheDocument();
  });

  it("renders form fields for each global variable", () => {
    renderForm();
    expect(screen.getByText("{{city}}")).toBeInTheDocument();
    expect(screen.getByText("{{country}}")).toBeInTheDocument();
  });

  it("shows 'Save' label when pendingRun is false", () => {
    renderForm();
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  it("shows 'Save & Run Workflow' label when pendingRun is true", () => {
    renderForm({}, { pendingRun: true });
    expect(
      screen.getByRole("button", { name: "Save & Run Workflow" }),
    ).toBeInTheDocument();
  });

  it("save button is disabled when isDirty is false", () => {
    renderForm({ isDirty: false });
    const btn = screen.getByRole("button", { name: "Save" });
    expect(btn).toBeDisabled();
  });

  describe("handleSave", () => {
    it("early returns when cellMap is null", async () => {
      renderForm({ cellMap: null, isDirty: true });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      // Should not call updateCell
      await waitFor(() => {
        expect(mockUpdateCell).not.toHaveBeenCalled();
      });
    });

    it("early returns when graphId is missing", async () => {
      renderForm({ graphId: null, isDirty: true });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => {
        expect(mockUpdateCell).not.toHaveBeenCalled();
      });
    });

    it("skips unchanged fields", async () => {
      // formValues match globalVariables exactly → no updates
      renderForm({
        formValues: { city: "Tokyo", country: "Japan" },
        isDirty: true,
      });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => {
        expect(mockUpdateCell).not.toHaveBeenCalled();
      });
    });

    it("calls updateCell only for changed fields", async () => {
      renderForm({
        formValues: { city: "Osaka", country: "Japan" },
        isDirty: true,
      });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => {
        expect(mockUpdateCell).toHaveBeenCalledTimes(1);
        expect(mockUpdateCell).toHaveBeenCalledWith({
          graphId: "graph-1",
          cellId: "cell-1",
          value: "Osaka",
        });
      });
    });

    it("on success: updates store, shows snackbar, closes drawer", async () => {
      const { enqueueSnackbar } = await import("notistack");
      renderForm({
        formValues: { city: "Osaka", country: "Japan" },
        isDirty: true,
      });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => {
        const state = useGlobalVariablesDrawerStore.getState();
        expect(state.globalVariables).toEqual({
          city: "Osaka",
          country: "Japan",
        });
        expect(state.open).toBe(false);
        expect(enqueueSnackbar).toHaveBeenCalledWith(
          "Variables saved successfully",
          { variant: "success" },
        );
      });
    });

    it("with pendingRun=true: clears pending and runs workflow", async () => {
      renderForm(
        { formValues: { city: "Osaka", country: "Japan" }, isDirty: true },
        { pendingRun: true },
      );
      fireEvent.click(
        screen.getByRole("button", { name: "Save & Run Workflow" }),
      );
      await waitFor(() => {
        expect(useGlobalVariablesDrawerStore.getState().pendingRun).toBe(false);
        expect(mockRunWorkflow).toHaveBeenCalled();
      });
    });

    it("on error: shows error snackbar", async () => {
      mockUpdateCell.mockRejectedValue(new Error("Network error"));
      const { enqueueSnackbar } = await import("notistack");

      renderForm({
        formValues: { city: "Osaka", country: "Japan" },
        isDirty: true,
      });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => {
        expect(enqueueSnackbar).toHaveBeenCalledWith(
          "Failed to save variables",
          { variant: "error" },
        );
      });
    });
  });
});

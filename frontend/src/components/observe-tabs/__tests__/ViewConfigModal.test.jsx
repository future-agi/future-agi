import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";
import { ObserveHeaderContext } from "src/sections/project/context/ObserveHeaderContext";
import ViewConfigModal from "../ViewConfigModal";

// Mock the API hooks — expose mockCreate/mockUpdate so tests can assert on payloads
const mockCreate = vi.fn();
const mockUpdate = vi.fn();
vi.mock("src/api/project/saved-views", () => ({
  useCreateSavedView: () => ({ mutate: mockCreate, isPending: false }),
  useUpdateSavedView: () => ({ mutate: mockUpdate, isPending: false }),
}));

describe("ViewConfigModal", () => {
  const defaultProps = {
    open: true,
    onClose: vi.fn(),
    mode: "create",
    projectId: "test-project-id",
  };

  it("renders Create New View title in create mode", () => {
    render(<ViewConfigModal {...defaultProps} />);
    expect(screen.getByText("Create New View")).toBeInTheDocument();
  });

  it("renders Edit View title in edit mode", () => {
    render(
      <ViewConfigModal
        {...defaultProps}
        mode="edit"
        initialValues={{ id: "123", name: "Test", tab_type: "traces" }}
      />,
    );
    expect(screen.getByText("Edit View")).toBeInTheDocument();
  });

  it("renders name input field", () => {
    render(<ViewConfigModal {...defaultProps} />);
    expect(screen.getByLabelText("Name *")).toBeInTheDocument();
  });

  it("renders type selector", () => {
    render(<ViewConfigModal {...defaultProps} />);
    // MUI Select renders as a combobox role
    expect(screen.getByRole("combobox")).toBeInTheDocument();
  });

  it("renders visibility radio buttons", () => {
    render(<ViewConfigModal {...defaultProps} />);
    expect(screen.getByLabelText("Personal")).toBeInTheDocument();
    expect(screen.getByLabelText("Shared with team")).toBeInTheDocument();
  });

  it("renders Cancel and Create buttons", () => {
    render(<ViewConfigModal {...defaultProps} />);
    expect(screen.getByText("Cancel")).toBeInTheDocument();
    expect(screen.getByText("Create")).toBeInTheDocument();
  });

  it("does not render when open is false", () => {
    render(<ViewConfigModal {...defaultProps} open={false} />);
    expect(screen.queryByText("Create New View")).not.toBeInTheDocument();
  });
});

const defaultProps = {
  open: true,
  onClose: vi.fn(),
  mode: "create",
  projectId: "test-project-id",
};

const renderWithCtx = (getViewConfig, props) =>
  render(
    <ObserveHeaderContext.Provider
      value={{
        headerConfig: {},
        setHeaderConfig: () => {},
        activeViewConfig: null,
        setActiveViewConfig: () => {},
        registerGetViewConfig: () => {},
        getViewConfig,
      }}
    >
      <ViewConfigModal {...defaultProps} {...props} />
    </ObserveHeaderContext.Provider>,
  );

describe("ViewConfigModal — config snapshot on save", () => {
  beforeEach(() => {
    mockCreate.mockReset();
    mockUpdate.mockReset();
  });

  it("create mode sends getViewConfig() output as config", async () => {
    const snapshot = { filters: [{ columnId: "status" }] };
    renderWithCtx(() => snapshot);
    fireEvent.change(screen.getByLabelText("Name *"), {
      target: { value: "v1" },
    });
    fireEvent.click(screen.getByText("Create"));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].config).toEqual(snapshot);
  });

  it("create mode falls back to {} when getViewConfig returns null", async () => {
    renderWithCtx(() => null);
    fireEvent.change(screen.getByLabelText("Name *"), {
      target: { value: "v2" },
    });
    fireEvent.click(screen.getByText("Create"));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].config).toEqual({});
  });

  it("edit mode re-captures live config on save", async () => {
    const fresh = { filters: [{ columnId: "duration" }] };
    const stale = { filters: [{ columnId: "status" }] };
    renderWithCtx(() => fresh, {
      mode: "edit",
      initialValues: { id: "v9", name: "Old", tab_type: "traces", config: stale },
    });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][0].config).toEqual(fresh);
  });

  it("edit mode falls back to initialValues.config when getViewConfig returns null", async () => {
    const stale = { filters: [{ columnId: "status" }] };
    renderWithCtx(() => null, {
      mode: "edit",
      initialValues: { id: "v9", name: "Old", tab_type: "traces", config: stale },
    });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][0].config).toEqual(stale);
  });
});

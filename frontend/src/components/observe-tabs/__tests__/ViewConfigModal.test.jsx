import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";
import { ObserveHeaderContext } from "src/sections/project/context/ObserveHeaderContext";
import { enqueueSnackbar } from "notistack";
import ViewConfigModal from "../ViewConfigModal";

// Mock the API hooks — expose mockCreate/mockUpdate so tests can assert on payloads.
// `savedViewsResult` is mutable so duplicate-name tests can seed existing views.
const mockCreate = vi.fn();
const mockUpdate = vi.fn();
let savedViewsResult = { custom_views: [] };
vi.mock("src/api/project/saved-views", () => ({
  useCreateSavedView: () => ({ mutate: mockCreate, isPending: false }),
  useUpdateSavedView: () => ({ mutate: mockUpdate, isPending: false }),
  useGetSavedViews: () => ({ data: savedViewsResult }),
  // Mirror of the real helper (the factory replaces the whole module).
  getOwnViewNames: (views, userId) =>
    (views ?? [])
      .filter((v) => userId && String(v.created_by?.id) === String(userId))
      .map((v) => v.name),
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

// Current user for the per-user duplicate scope.
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "u1" } }),
}));

const ownView = (id, name, tabType = "traces") => ({
  id,
  name,
  tab_type: tabType,
  created_by: { id: "u1" },
});

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

const canonicalFilter = (columnId) => ({
  column_id: columnId,
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: "ERROR",
  },
});

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
    savedViewsResult = { custom_views: [] };
  });

  it("create mode sends getViewConfig() output as config", async () => {
    const snapshot = { filters: [canonicalFilter("status")] };
    renderWithCtx(() => snapshot);
    fireEvent.change(screen.getByLabelText("Name *"), {
      target: { value: "v1" },
    });
    fireEvent.click(screen.getByText("Create"));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][0].config).toEqual(snapshot);
    expect(mockCreate.mock.calls[0][0].tab_type).toBe("traces");
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
    const fresh = { filters: [canonicalFilter("duration")] };
    const stale = { filters: [canonicalFilter("status")] };
    renderWithCtx(() => fresh, {
      mode: "edit",
      initialValues: {
        id: "v9",
        name: "Old",
        tab_type: "traces",
        config: stale,
      },
    });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][0].config).toEqual(fresh);
    expect(mockUpdate.mock.calls[0][0]).not.toHaveProperty("tab_type");
  });

  it("edit mode falls back to initialValues.config when getViewConfig returns null", async () => {
    const stale = { filters: [canonicalFilter("status")] };
    renderWithCtx(() => null, {
      mode: "edit",
      initialValues: {
        id: "v9",
        name: "Old",
        tab_type: "traces",
        config: stale,
      },
    });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][0].config).toEqual(stale);
    expect(mockUpdate.mock.calls[0][0]).not.toHaveProperty("tab_type");
  });
});

describe("ViewConfigModal — duplicate-name guard", () => {
  beforeEach(() => {
    mockCreate.mockReset();
    mockUpdate.mockReset();
    savedViewsResult = { custom_views: [] };
    enqueueSnackbar.mockClear?.();
  });

  it("blocks submit and shows inline error for an exact duplicate name", async () => {
    savedViewsResult = { custom_views: [ownView("1", "My View")] };
    renderWithCtx(() => ({}));
    fireEvent.change(screen.getByLabelText("Name *"), {
      target: { value: "My View" },
    });
    expect(
      screen.getByText("A view with this name already exists."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("Create"));
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("allows a case-variant name (backend uniqueness is case-sensitive)", async () => {
    savedViewsResult = { custom_views: [ownView("1", "My View")] };
    renderWithCtx(() => ({}));
    fireEvent.change(screen.getByLabelText("Name *"), {
      target: { value: "my view" },
    });
    fireEvent.click(screen.getByText("Create"));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
  });

  it("allows a name that only another user's shared view holds (per-user scope)", async () => {
    savedViewsResult = {
      custom_views: [
        { id: "1", name: "Latency", tab_type: "traces", created_by: { id: "u2" } },
      ],
    };
    renderWithCtx(() => ({}));
    fireEvent.change(screen.getByLabelText("Name *"), {
      target: { value: "Latency" },
    });
    fireEvent.click(screen.getByText("Create"));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
  });

  it("rejects a duplicate name even across a different tab_type (project scope)", async () => {
    // Backend project-scope uniqueness ignores tab_type, so the modal must too.
    savedViewsResult = { custom_views: [ownView("1", "My View", "voice")] };
    renderWithCtx(() => ({}));
    fireEvent.change(screen.getByLabelText("Name *"), {
      target: { value: "My View" },
    });
    fireEvent.click(screen.getByText("Create"));
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("allows a unique name and submits", async () => {
    savedViewsResult = { custom_views: [ownView("1", "Existing")] };
    renderWithCtx(() => ({}));
    fireEvent.change(screen.getByLabelText("Name *"), {
      target: { value: "Brand New" },
    });
    fireEvent.click(screen.getByText("Create"));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
  });

  it("edit mode blocks renaming onto another view's name", () => {
    savedViewsResult = {
      custom_views: [ownView("self", "Mine"), ownView("other", "Taken")],
    };
    renderWithCtx(() => ({}), {
      mode: "edit",
      initialValues: { id: "self", name: "Mine", tab_type: "traces" },
    });
    fireEvent.change(screen.getByLabelText("Name *"), {
      target: { value: "Taken" },
    });
    expect(
      screen.getByText("A view with this name already exists."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("Save"));
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it("edit mode allows keeping the view's own name (self excluded)", async () => {
    savedViewsResult = { custom_views: [ownView("self", "Mine")] };
    renderWithCtx(() => ({}), {
      mode: "edit",
      initialValues: { id: "self", name: "Mine", tab_type: "traces" },
    });
    // Name field already holds "Mine"; save should not be blocked.
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
  });

  it("surfaces the backend error message via snackbar on a create 400", async () => {
    renderWithCtx(() => ({}));
    fireEvent.change(screen.getByLabelText("Name *"), {
      target: { value: "Race View" },
    });
    fireEvent.click(screen.getByText("Create"));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    // Invoke the onError callback the component supplied to the mutation.
    const { onError } = mockCreate.mock.calls[0][1];
    onError({
      response: { data: { message: "A view named 'Race View' already exists." } },
    });
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "A view named 'Race View' already exists.",
      { variant: "error" },
    );
  });
});

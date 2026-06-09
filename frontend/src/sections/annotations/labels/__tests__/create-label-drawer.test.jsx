import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import CreateLabelDrawer from "../create-label-drawer";

// Mock the API hooks
const mockCreate = vi.fn();
const mockUpdate = vi.fn();

vi.mock("src/api/annotation-labels/annotation-labels", () => ({
  useCreateAnnotationLabel: () => ({
    mutate: mockCreate,
    isPending: false,
  }),
  useUpdateAnnotationLabel: () => ({
    mutate: mockUpdate,
    isPending: false,
  }),
}));

// Mock Iconify
vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

// Mock type-specific settings components
vi.mock("../settings/categorical-settings", () => ({
  default: () => (
    <div data-testid="categorical-settings">Categorical Settings</div>
  ),
}));
vi.mock("../settings/numeric-settings", () => ({
  default: () => <div data-testid="numeric-settings">Numeric Settings</div>,
}));
vi.mock("../settings/text-settings", () => ({
  default: () => <div data-testid="text-settings">Text Settings</div>,
}));
vi.mock("../settings/star-settings", () => ({
  default: () => <div data-testid="star-settings">Star Settings</div>,
}));

describe("CreateLabelDrawer", () => {
  const onClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not render content when closed", () => {
    render(
      <CreateLabelDrawer open={false} onClose={onClose} editLabel={null} />,
    );
    // When Drawer is closed, MUI may not render the content at all
    expect(screen.queryByText("Create Label")).toBeNull();
  });

  it("renders create mode when open with no editLabel", () => {
    render(
      <CreateLabelDrawer open={true} onClose={onClose} editLabel={null} />,
    );

    expect(screen.getByText("Create Label")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /create$/i }),
    ).toBeInTheDocument();
  });

  it("renders edit mode when editLabel has an id", () => {
    const editLabel = {
      id: "1",
      name: "Accuracy",
      type: "categorical",
      description: "Desc",
      settings: {
        options: [{ label: "A" }],
        multi_choice: false,
        rule_prompt: "",
        auto_annotate: false,
        strategy: null,
      },
    };
    render(
      <CreateLabelDrawer open={true} onClose={onClose} editLabel={editLabel} />,
    );

    expect(screen.getByText("Edit Label")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save$/i })).toBeInTheDocument();
  });

  it("shows duplicate mode as create (no id)", () => {
    const dupLabel = {
      id: undefined,
      name: "Copy of Accuracy",
      type: "categorical",
      _isDuplicate: true,
    };
    render(
      <CreateLabelDrawer open={true} onClose={onClose} editLabel={dupLabel} />,
    );

    // Title should be Create, not Edit
    expect(screen.getByText("Create Label")).toBeInTheDocument();
  });

  it("renders all 5 label type radio options", () => {
    render(
      <CreateLabelDrawer open={true} onClose={onClose} editLabel={null} />,
    );

    expect(screen.getByText("Categorical")).toBeInTheDocument();
    expect(screen.getByText("Numeric")).toBeInTheDocument();
    expect(screen.getByText("Text")).toBeInTheDocument();
    expect(screen.getByText("Star Rating")).toBeInTheDocument();
    expect(screen.getByText("Thumbs Up/Down")).toBeInTheDocument();
  });

  it("shows categorical settings by default", () => {
    render(
      <CreateLabelDrawer open={true} onClose={onClose} editLabel={null} />,
    );

    expect(screen.getByTestId("categorical-settings")).toBeInTheDocument();
  });

  it("switches settings when type changes", async () => {
    const user = userEvent.setup();
    render(
      <CreateLabelDrawer open={true} onClose={onClose} editLabel={null} />,
    );

    // Click "Numeric" radio
    await user.click(screen.getByLabelText(/Numeric/));

    await waitFor(() => {
      expect(screen.getByTestId("numeric-settings")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("categorical-settings"),
    ).not.toBeInTheDocument();
  });

  it("shows 'no config needed' for thumbs_up_down", async () => {
    const user = userEvent.setup();
    render(
      <CreateLabelDrawer open={true} onClose={onClose} editLabel={null} />,
    );

    await user.click(screen.getByLabelText(/Thumbs Up\/Down/));

    await waitFor(() => {
      expect(
        screen.getByText("No additional configuration needed."),
      ).toBeInTheDocument();
    });
  });

  it("disables type radio buttons in edit mode", () => {
    const editLabel = {
      id: "1",
      name: "Test",
      type: "numeric",
      settings: { min: 0, max: 10, step_size: 1, display_type: "slider" },
    };
    render(
      <CreateLabelDrawer open={true} onClose={onClose} editLabel={editLabel} />,
    );

    const radios = screen.getAllByRole("radio");
    radios.forEach((radio) => {
      expect(radio).toBeDisabled();
    });
  });

  it("shows '(cannot be changed)' for type in edit mode", () => {
    const editLabel = {
      id: "1",
      name: "Test",
      type: "numeric",
      settings: {},
    };
    render(
      <CreateLabelDrawer open={true} onClose={onClose} editLabel={editLabel} />,
    );

    expect(screen.getByText(/cannot be changed/)).toBeInTheDocument();
  });

  it("calls onClose when Cancel is clicked", async () => {
    const user = userEvent.setup();
    render(
      <CreateLabelDrawer open={true} onClose={onClose} editLabel={null} />,
    );

    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("calls createLabel on submit in create mode", async () => {
    const user = userEvent.setup();
    render(
      <CreateLabelDrawer open={true} onClose={onClose} editLabel={null} />,
    );

    // Fill in name
    const nameField = screen.getByLabelText(/name/i);
    await user.clear(nameField);
    await user.type(nameField, "New Label");

    // Submit
    await user.click(screen.getByRole("button", { name: /create$/i }));

    await waitFor(() => {
      expect(mockCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "New Label",
          type: "categorical",
        }),
        expect.any(Object),
      );
    });
  });

  it("passes the created label back to the caller", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    mockCreate.mockImplementationOnce((_payload, options) => {
      options.onSuccess({
        data: { id: "created-label", name: "Created Label" },
      });
    });

    render(
      <CreateLabelDrawer
        open={true}
        onClose={onClose}
        editLabel={null}
        onCreated={onCreated}
      />,
    );

    await user.type(screen.getByLabelText(/name/i), "Created Label");
    await user.click(screen.getByRole("button", { name: /create$/i }));

    await waitFor(() => {
      expect(onCreated).toHaveBeenCalledWith({
        id: "created-label",
        name: "Created Label",
      });
      expect(onClose).toHaveBeenCalledOnce();
    });
  });

  it("calls updateLabel on submit in edit mode", async () => {
    const user = userEvent.setup();
    const editLabel = {
      id: "label-123",
      name: "Old Name",
      type: "text",
      description: "Old desc",
      settings: { placeholder: "Enter...", max_length: 500, min_length: 0 },
    };
    render(
      <CreateLabelDrawer open={true} onClose={onClose} editLabel={editLabel} />,
    );

    // Modify name
    const nameField = screen.getByLabelText(/name/i);
    await user.clear(nameField);
    await user.type(nameField, "Updated Name");

    // Submit
    await user.click(screen.getByRole("button", { name: /save$/i }));

    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalledWith(
        expect.objectContaining({
          id: "label-123",
          name: "Updated Name",
          type: "text",
        }),
        expect.any(Object),
      );
    });
  });

  it("pre-fills form with editLabel data", () => {
    const editLabel = {
      id: "1",
      name: "Accuracy",
      type: "categorical",
      description: "A description",
      settings: { options: [{ label: "A" }] },
    };
    render(
      <CreateLabelDrawer open={true} onClose={onClose} editLabel={editLabel} />,
    );

    expect(screen.getByLabelText(/name/i)).toHaveValue("Accuracy");
    expect(screen.getByLabelText(/description/i)).toHaveValue("A description");
  });
});

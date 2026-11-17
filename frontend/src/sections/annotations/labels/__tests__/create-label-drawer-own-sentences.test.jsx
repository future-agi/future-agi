import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import CreateLabelDrawer from "../create-label-drawer";

// The drawer is rendered with its real settings forms, so every field it
// shows carries the same attributes as on the Labels page.
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

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

const MIN_SENTENCE = "Enter a value of 0 or more";
const ABOVE_ZERO_SENTENCE = "Enter a value above 0";
const NAME_SENTENCE = "Name is required";
const FEWEST_STARS_SENTENCE = "Minimum 1 star";
const MOST_STARS_SENTENCE = "Maximum 10 stars";
const WHOLE_STARS_SENTENCE = "Enter a whole number of stars.";

// The text a field shows under itself, as its helper text. Maximum and Step
// Size can show the same sentence, so each is read under its own field.
const underField = (label) =>
  within(screen.getByLabelText(label).closest(".MuiFormControl-root"));

const nameField = () => screen.getByRole("textbox", { name: /^name/i });

const setField = (label, value) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

const pressCreate = (user) =>
  user.click(screen.getByRole("button", { name: /create$/i }));

const openNumericLabel = async (user) => {
  render(<CreateLabelDrawer open={true} onClose={vi.fn()} editLabel={null} />);
  await user.type(nameField(), "Score");
  await user.click(screen.getByLabelText(/Numeric/));
  await waitFor(() =>
    expect(screen.getByLabelText(/minimum/i)).toBeInTheDocument(),
  );
};

// The Labels page's create drawer refuses a value in its own words, under the
// field, and sends nothing. The browser's own check never answers first.
describe("CreateLabelDrawer shows its own sentence for a refused value", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("asks for a Minimum of 0 or more when Min is -5, and sends nothing", async () => {
    const user = userEvent.setup();
    await openNumericLabel(user);

    setField(/minimum/i, "-5");
    setField(/maximum/i, "10");
    setField(/step size/i, "1");
    await pressCreate(user);

    await waitFor(() =>
      expect(underField(/minimum/i).queryByText(MIN_SENTENCE)).not.toBeNull(),
    );
    expect(screen.getByLabelText(/minimum/i)).toHaveValue(-5);
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("asks for a Maximum above 0 when Max is -3, and sends nothing", async () => {
    const user = userEvent.setup();
    await openNumericLabel(user);

    setField(/minimum/i, "0");
    setField(/maximum/i, "-3");
    setField(/step size/i, "1");
    await pressCreate(user);

    await waitFor(() =>
      expect(
        underField(/maximum/i).queryByText(ABOVE_ZERO_SENTENCE),
      ).not.toBeNull(),
    );
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("asks for a Step Size above 0 when Step is 0, and sends nothing", async () => {
    const user = userEvent.setup();
    await openNumericLabel(user);

    setField(/minimum/i, "0");
    setField(/maximum/i, "10");
    setField(/step size/i, "0");
    await pressCreate(user);

    await waitFor(() =>
      expect(
        underField(/step size/i).queryByText(ABOVE_ZERO_SENTENCE),
      ).not.toBeNull(),
    );
    expect(mockCreate).not.toHaveBeenCalled();
  });

  // Min -5, Max 0 and Step 0 pressed together, as a person did on the page.
  it("shows a sentence under Min, Max and Step at once, and sends nothing", async () => {
    const user = userEvent.setup();
    await openNumericLabel(user);

    setField(/minimum/i, "-5");
    setField(/maximum/i, "0");
    setField(/step size/i, "0");
    await pressCreate(user);

    await waitFor(() =>
      expect(underField(/minimum/i).queryByText(MIN_SENTENCE)).not.toBeNull(),
    );
    expect(
      underField(/maximum/i).queryByText(ABOVE_ZERO_SENTENCE),
    ).not.toBeNull();
    expect(
      underField(/step size/i).queryByText(ABOVE_ZERO_SENTENCE),
    ).not.toBeNull();
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("asks for a name when Name is empty, and sends nothing", async () => {
    const user = userEvent.setup();
    render(
      <CreateLabelDrawer open={true} onClose={vi.fn()} editLabel={null} />,
    );

    await user.click(screen.getByLabelText(/Thumbs Up\/Down/));
    await pressCreate(user);

    await waitFor(() =>
      expect(
        within(nameField().closest(".MuiFormControl-root")).queryByText(
          NAME_SENTENCE,
        ),
      ).not.toBeNull(),
    );
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("asks for at least 1 star when Number of Stars is 0, and sends nothing", async () => {
    const user = userEvent.setup();
    render(
      <CreateLabelDrawer open={true} onClose={vi.fn()} editLabel={null} />,
    );

    await user.type(nameField(), "Rating");
    await user.click(screen.getByLabelText(/Star Rating/));
    await waitFor(() =>
      expect(screen.getByLabelText(/number of stars/i)).toBeInTheDocument(),
    );

    setField(/number of stars/i, "0");
    await pressCreate(user);

    await waitFor(() =>
      expect(
        underField(/number of stars/i).queryByText(FEWEST_STARS_SENTENCE),
      ).not.toBeNull(),
    );
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("asks for a whole number when Number of Stars is 2.5, and sends nothing", async () => {
    const user = userEvent.setup();
    render(
      <CreateLabelDrawer open={true} onClose={vi.fn()} editLabel={null} />,
    );

    await user.type(nameField(), "Rating");
    await user.click(screen.getByLabelText(/Star Rating/));
    await waitFor(() =>
      expect(screen.getByLabelText(/number of stars/i)).toBeInTheDocument(),
    );

    setField(/number of stars/i, "2.5");
    await pressCreate(user);

    await waitFor(() =>
      expect(
        underField(/number of stars/i).queryByText(WHOLE_STARS_SENTENCE),
      ).not.toBeNull(),
    );
    expect(mockCreate).not.toHaveBeenCalled();
  });

  // More than 10 stars cannot be typed in the field, so a label stored with
  // 12 is the way a person meets this limit.
  it("asks for at most 10 stars when a stored label has 12, and saves nothing", async () => {
    const user = userEvent.setup();
    const editLabel = {
      id: "label-12-stars",
      name: "Rating",
      type: "star",
      settings: { no_of_stars: 12 },
    };
    render(
      <CreateLabelDrawer open={true} onClose={vi.fn()} editLabel={editLabel} />,
    );

    expect(screen.getByLabelText(/number of stars/i)).toHaveValue(12);
    await user.click(screen.getByRole("button", { name: /save$/i }));

    await waitFor(() =>
      expect(
        underField(/number of stars/i).queryByText(MOST_STARS_SENTENCE),
      ).not.toBeNull(),
    );
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it("still creates a numeric label with Min 1, Max 5 and Step 0.5", async () => {
    const user = userEvent.setup();
    await openNumericLabel(user);

    setField(/minimum/i, "1");
    setField(/maximum/i, "5");
    setField(/step size/i, "0.5");
    await pressCreate(user);

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Score",
        type: "numeric",
        settings: expect.objectContaining({ min: 1, max: 5, step_size: 0.5 }),
      }),
      expect.any(Object),
    );
  });
});

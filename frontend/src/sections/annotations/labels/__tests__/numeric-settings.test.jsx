/* eslint-disable react/prop-types */
import { describe, expect, it, vi } from "vitest";
import { Button } from "@mui/material";
import { useForm } from "react-hook-form";
import { enqueueSnackbar } from "notistack";
import {
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import NumericSettings from "../settings/numeric-settings";

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// Both numeric label forms refuse a bad Minimum, Maximum or Step Size in
// these same words.
const MIN_SENTENCE = "Enter a value of 0 or more";
const MAX_SENTENCE = "Enter a value above 0";
const STEP_SENTENCE = "Enter a value above 0";
const ORDER_SENTENCE = "Max must be greater than min";
const BUTTON_SCALE_SENTENCE =
  "A button scale can show up to 11 choices. Use a larger step or the slider.";

// The text a field shows under itself, as its helper text. Maximum and Step
// Size can show the same sentence, so each is read under its own field.
const underField = (label) =>
  within(screen.getByLabelText(label).closest(".MuiFormControl-root"));

const validate = (user) =>
  user.click(screen.getByRole("button", { name: /validate/i }));

const save = (user) => user.click(screen.getByRole("button", { name: "Save" }));

// The settings the form handed to its submit handler.
const savedSettings = (onSubmit) => onSubmit.mock.calls[0][0].settings;

function NumericSettingsHarness({ defaultValues, onSubmit }) {
  const methods = useForm({ defaultValues });
  return (
    <form onSubmit={methods.handleSubmit(onSubmit)}>
      <NumericSettings control={methods.control} />
      <Button type="button" onClick={() => methods.trigger()}>
        Validate
      </Button>
      <Button type="submit">Save</Button>
    </form>
  );
}

describe("NumericSettings", () => {
  it("rejects negative bounds and non-positive step size", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(
      <NumericSettingsHarness
        defaultValues={{
          settings: {
            min: -1,
            max: -2,
            step_size: 0,
            display_type: "button",
          },
        }}
        onSubmit={onSubmit}
      />,
    );

    await validate(user);

    await waitFor(() =>
      expect(underField(/minimum/i).queryByText(MIN_SENTENCE)).not.toBeNull(),
    );
    expect(underField(/maximum/i).queryByText(MAX_SENTENCE)).not.toBeNull();
    expect(underField(/step size/i).queryByText(STEP_SENTENCE)).not.toBeNull();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  // Typed key by key: "-" first, then "5", so the field reads "-5".
  it("keeps a minus typed in Minimum and asks for 0 or more", async () => {
    const user = userEvent.setup();

    render(
      <NumericSettingsHarness
        defaultValues={{
          settings: { min: 0, max: 10, step_size: 1, display_type: "slider" },
        }}
        onSubmit={vi.fn()}
      />,
    );

    const minInput = screen.getByLabelText(/minimum/i);
    await user.clear(minInput);
    await user.type(minInput, "-5");
    await validate(user);

    expect(minInput).toHaveValue(-5);
    await waitFor(() =>
      expect(underField(/minimum/i).queryByText(MIN_SENTENCE)).not.toBeNull(),
    );
  });

  it("asks for a Maximum above 0 when it is 0", async () => {
    const user = userEvent.setup();

    render(
      <NumericSettingsHarness
        defaultValues={{
          settings: { min: 0, max: 0, step_size: 1, display_type: "slider" },
        }}
        onSubmit={vi.fn()}
      />,
    );

    await validate(user);

    await waitFor(() =>
      expect(underField(/maximum/i).queryByText(MAX_SENTENCE)).not.toBeNull(),
    );
    expect(underField(/maximum/i).queryByText(ORDER_SENTENCE)).toBeNull();
  });

  it("keeps a minus typed in Maximum and asks for a value above 0", async () => {
    const user = userEvent.setup();

    render(
      <NumericSettingsHarness
        defaultValues={{
          settings: { min: 0, max: 10, step_size: 1, display_type: "slider" },
        }}
        onSubmit={vi.fn()}
      />,
    );

    const maxInput = screen.getByLabelText(/maximum/i);
    await user.clear(maxInput);
    await user.type(maxInput, "-3");
    await validate(user);

    expect(maxInput).toHaveValue(-3);
    await waitFor(() =>
      expect(underField(/maximum/i).queryByText(MAX_SENTENCE)).not.toBeNull(),
    );
  });

  it("asks for a Step Size above 0 when it is 0", async () => {
    const user = userEvent.setup();

    render(
      <NumericSettingsHarness
        defaultValues={{
          settings: { min: 0, max: 10, step_size: 0, display_type: "slider" },
        }}
        onSubmit={vi.fn()}
      />,
    );

    await validate(user);

    await waitFor(() =>
      expect(
        underField(/step size/i).queryByText(STEP_SENTENCE),
      ).not.toBeNull(),
    );
  });

  it("still says Max must be greater than min for Max 3 and Min 5", async () => {
    const user = userEvent.setup();

    render(
      <NumericSettingsHarness
        defaultValues={{
          settings: { min: 5, max: 3, step_size: 1, display_type: "slider" },
        }}
        onSubmit={vi.fn()}
      />,
    );

    await validate(user);

    await waitFor(() =>
      expect(underField(/maximum/i).queryByText(ORDER_SENTENCE)).not.toBeNull(),
    );
    expect(underField(/maximum/i).queryByText(MAX_SENTENCE)).toBeNull();
  });

  it("does not keep a leading zero when typing over the default value", async () => {
    const user = userEvent.setup();

    render(
      <NumericSettingsHarness
        defaultValues={{
          settings: { min: 0, max: 10, step_size: 1, display_type: "slider" },
        }}
        onSubmit={vi.fn()}
      />,
    );

    const minInput = screen.getByLabelText(/minimum/i);
    await user.type(minInput, "5");

    expect(minInput).toHaveValue(5);
  });

  it("saves a slider with Maximum 100 and gives no warning", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    enqueueSnackbar.mockClear();

    render(
      <NumericSettingsHarness
        defaultValues={{
          settings: { min: 0, max: 1, step_size: 1, display_type: "slider" },
        }}
        onSubmit={onSubmit}
      />,
    );

    const maxInput = screen.getByLabelText(/maximum/i);
    await user.clear(maxInput);
    await user.type(maxInput, "100");

    expect(maxInput).toHaveValue(100);
    expect(enqueueSnackbar).not.toHaveBeenCalled();

    await save(user);

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(savedSettings(onSubmit)).toEqual({
      min: 0,
      max: 100,
      step_size: 1,
      display_type: "slider",
    });
  });

  // Saved with the Save button, so the browser's own number check runs too.
  it("saves a Minimum of 0.5 and a Maximum of 2.5", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(
      <NumericSettingsHarness
        defaultValues={{
          settings: { min: 0, max: 2, step_size: 1, display_type: "slider" },
        }}
        onSubmit={onSubmit}
      />,
    );

    fireEvent.change(screen.getByLabelText(/minimum/i), {
      target: { value: "0.5" },
    });
    fireEvent.change(screen.getByLabelText(/maximum/i), {
      target: { value: "2.5" },
    });
    await save(user);

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(savedSettings(onSubmit)).toEqual({
      min: 0.5,
      max: 2.5,
      step_size: 1,
      display_type: "slider",
    });
  });
});

// A button scale shows at most 11 choices, counted as the annotate screen
// draws the buttons. The reason shows under Step Size.
describe("NumericSettings shown as buttons", () => {
  // 0 to 20 in steps of 1 draws 21 buttons on the annotate screen.
  it("refuses 21 choices under Step Size and saves nothing", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(
      <NumericSettingsHarness
        defaultValues={{
          settings: { min: 0, max: 10, step_size: 1, display_type: "button" },
        }}
        onSubmit={onSubmit}
      />,
    );

    const maxInput = screen.getByLabelText(/maximum/i);
    await user.clear(maxInput);
    await user.type(maxInput, "20");
    await save(user);

    await waitFor(() =>
      expect(
        underField(/step size/i).queryByText(BUTTON_SCALE_SENTENCE),
      ).not.toBeNull(),
    );
    expect(
      underField(/maximum/i).queryByText(BUTTON_SCALE_SENTENCE),
    ).toBeNull();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  // 0 to 5 in steps of 0.5 draws 11 buttons: the most a button scale shows.
  it("saves a Step Size of 0.5 with Maximum 5", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(
      <NumericSettingsHarness
        defaultValues={{
          settings: { min: 0, max: 5, step_size: 1, display_type: "button" },
        }}
        onSubmit={onSubmit}
      />,
    );

    fireEvent.change(screen.getByLabelText(/step size/i), {
      target: { value: "0.5" },
    });
    await save(user);

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(savedSettings(onSubmit)).toEqual({
      min: 0,
      max: 5,
      step_size: 0.5,
      display_type: "button",
    });
    expect(
      underField(/step size/i).queryByText(BUTTON_SCALE_SENTENCE),
    ).toBeNull();
  });
});

// A button label saved earlier with more than 11 choices still opens; an edit
// to it is refused until the step or the display type changes. The labels
// drawer opens an existing label by passing its saved settings to the form
// as they are, as this harness does with its default values.
describe("NumericSettings editing a button label with 21 choices", () => {
  const savedButtonLabel = {
    settings: { min: 0, max: 20, step_size: 1, display_type: "button" },
  };

  it("opens, refuses a save, then saves with a step of 2", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(
      <NumericSettingsHarness
        defaultValues={savedButtonLabel}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByLabelText(/maximum/i)).toHaveValue(20);
    expect(screen.getByLabelText(/step size/i)).toHaveValue(1);

    await save(user);

    await waitFor(() =>
      expect(
        underField(/step size/i).queryByText(BUTTON_SCALE_SENTENCE),
      ).not.toBeNull(),
    );
    expect(onSubmit).not.toHaveBeenCalled();

    const stepInput = screen.getByLabelText(/step size/i);
    await user.clear(stepInput);
    await user.type(stepInput, "2");
    await save(user);

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(savedSettings(onSubmit)).toEqual({
      min: 0,
      max: 20,
      step_size: 2,
      display_type: "button",
    });
  });

  it("refuses a save, then saves as a slider", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(
      <NumericSettingsHarness
        defaultValues={savedButtonLabel}
        onSubmit={onSubmit}
      />,
    );

    await save(user);

    await waitFor(() =>
      expect(
        underField(/step size/i).queryByText(BUTTON_SCALE_SENTENCE),
      ).not.toBeNull(),
    );
    expect(onSubmit).not.toHaveBeenCalled();

    await user.click(screen.getByRole("radio", { name: "Slider" }));
    await save(user);

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(savedSettings(onSubmit)).toEqual({
      min: 0,
      max: 20,
      step_size: 1,
      display_type: "slider",
    });
  });
});

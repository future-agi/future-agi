import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import CreateEditLabel from "../CreateEditLabel";

// Both numeric label forms allow decimals for Min, Max and Step Size, put no
// cap on Max, and keep a button scale to 11 choices, counted as the annotate
// screen draws the buttons. The reason shows under Step Size.
const BUTTON_SCALE_SENTENCE =
  "A button scale can show up to 11 choices. Use a larger step or the slider.";
const INTEGER_SENTENCE = "Value must be an integer (no decimal places)";

const PLACEHOLDERS = {
  min: "Enter min value",
  max: "Enter max value",
  stepSize: "Enter stepsize",
};

const numericLabel = (settings) => ({
  id: "label-numeric",
  name: "Score",
  type: "numeric",
  description: "",
  settings: { displayType: "slider", ...settings },
});

let queryClient;

const field = (name) => screen.getByPlaceholderText(PLACEHOLDERS[name]);

// The text a number field shows under itself, as its helper text.
const underField = (name) =>
  within(field(name).closest(".MuiFormControl-root"));

const openLabelForEditing = (label) => {
  const put = vi
    .spyOn(axios, "put")
    .mockResolvedValue({ data: { result: label } });
  const post = vi
    .spyOn(axios, "post")
    .mockResolvedValue({ data: { result: label } });
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <SnackbarProvider>
        <CreateEditLabel
          open
          onClose={vi.fn()}
          projectId="project-numeric"
          onSuccess={vi.fn()}
          editData={label}
        />
      </SnackbarProvider>
    </QueryClientProvider>,
  );
  return { put, post };
};

const typeValue = async (name, value) => {
  await act(async () => {
    fireEvent.change(field(name), { target: { value } });
  });
};

// The form checks the values on promises; one timer tick lets that check
// finish and any reason render, so a reason that is missing is really missing.
const settleValidation = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

// Submits the form itself, so the form's own check decides whether anything
// is sent, not the Update button being disabled.
const submitForm = async () => {
  await act(async () => {
    fireEvent.submit(field("min").closest("form"));
  });
  await settleValidation();
};

// Picks a display type in the Display Options select.
const chooseDisplay = async (name) => {
  const select = within(
    screen.getByText("Display Options").closest(".MuiFormControl-root"),
  ).getByRole("combobox");
  await act(async () => {
    fireEvent.mouseDown(select);
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("option", { name }));
  });
};

const expectSaved = async (put, label, settings) => {
  await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
  expect(put).toHaveBeenCalledWith(endpoints.project.updateLabel(label.id), {
    name: "Score",
    type: "numeric",
    description: "",
    project: "project-numeric",
    settings,
  });
};

describe("a numeric label's decimals and Max", () => {
  afterEach(() => {
    cleanup();
    queryClient?.clear();
    vi.restoreAllMocks();
  });

  it("saves a Step Size of 0.5 with Min 0 and Max 5", async () => {
    const label = numericLabel({ min: 0, max: 5, stepSize: 1 });
    const { put, post } = openLabelForEditing(label);

    await typeValue("stepSize", "0.5");
    await submitForm();

    expect(underField("stepSize").queryByText(INTEGER_SENTENCE)).toBeNull();
    await expectSaved(put, label, {
      min: 0,
      max: 5,
      step_size: 0.5,
      display_type: "slider",
    });
    expect(post).not.toHaveBeenCalled();
  });

  it("saves a Max of 2.5", async () => {
    const label = numericLabel({ min: 0, max: 2, stepSize: 1 });
    const { put, post } = openLabelForEditing(label);

    await typeValue("max", "2.5");
    await submitForm();

    expect(underField("max").queryByText(INTEGER_SENTENCE)).toBeNull();
    await expectSaved(put, label, {
      min: 0,
      max: 2.5,
      step_size: 1,
      display_type: "slider",
    });
    expect(post).not.toHaveBeenCalled();
  });

  it("saves a slider with Max 100", async () => {
    const label = numericLabel({ min: 0, max: 10, stepSize: 1 });
    const { put, post } = openLabelForEditing(label);

    await typeValue("max", "100");
    await submitForm();

    await expectSaved(put, label, {
      min: 0,
      max: 100,
      step_size: 1,
      display_type: "slider",
    });
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a numeric label shown as buttons", () => {
  afterEach(() => {
    cleanup();
    queryClient?.clear();
    vi.restoreAllMocks();
  });

  // 0 to 20 in steps of 1 draws 21 buttons on the annotate screen.
  it("refuses 21 choices under Step Size and sends nothing", async () => {
    const { put, post } = openLabelForEditing(
      numericLabel({ min: 0, max: 10, stepSize: 1, displayType: "button" }),
    );

    await typeValue("max", "20");
    await submitForm();

    expect(
      underField("stepSize").queryByText(BUTTON_SCALE_SENTENCE),
    ).not.toBeNull();
    expect(underField("max").queryByText(BUTTON_SCALE_SENTENCE)).toBeNull();
    expect(put).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  // 0 to 20 in steps of 2 draws 11 buttons: the most a button scale shows.
  it("saves 11 choices with a step of 2", async () => {
    const label = numericLabel({
      min: 0,
      max: 10,
      stepSize: 1,
      displayType: "button",
    });
    const { put, post } = openLabelForEditing(label);

    await typeValue("max", "20");
    await typeValue("stepSize", "2");
    await submitForm();

    expect(
      underField("stepSize").queryByText(BUTTON_SCALE_SENTENCE),
    ).toBeNull();
    await expectSaved(put, label, {
      min: 0,
      max: 20,
      step_size: 2,
      display_type: "button",
    });
    expect(post).not.toHaveBeenCalled();
  });
});

// A button label saved earlier with more than 11 choices still opens; an edit
// to it is refused until the step or the display type changes.
describe("an existing button label with 21 choices", () => {
  afterEach(() => {
    cleanup();
    queryClient?.clear();
    vi.restoreAllMocks();
  });

  const savedLabel = () =>
    numericLabel({ min: 0, max: 20, stepSize: 1, displayType: "button" });

  it("opens, refuses a save, then saves with a step of 2", async () => {
    const label = savedLabel();
    const { put, post } = openLabelForEditing(label);

    expect(field("max")).toHaveValue(20);
    expect(field("stepSize")).toHaveValue(1);

    await submitForm();

    expect(
      underField("stepSize").queryByText(BUTTON_SCALE_SENTENCE),
    ).not.toBeNull();
    expect(put).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();

    await typeValue("stepSize", "2");
    await submitForm();

    await expectSaved(put, label, {
      min: 0,
      max: 20,
      step_size: 2,
      display_type: "button",
    });
    expect(post).not.toHaveBeenCalled();
  });

  it("refuses a save with the sentence, then saves as a slider", async () => {
    const label = savedLabel();
    const { put, post } = openLabelForEditing(label);

    await submitForm();

    expect(
      underField("stepSize").queryByText(BUTTON_SCALE_SENTENCE),
    ).not.toBeNull();
    expect(put).not.toHaveBeenCalled();

    await chooseDisplay("Slider");
    await submitForm();

    await expectSaved(put, label, {
      min: 0,
      max: 20,
      step_size: 1,
      display_type: "slider",
    });
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a text label's Max length", () => {
  afterEach(() => {
    cleanup();
    queryClient?.clear();
    vi.restoreAllMocks();
  });

  // The text label form keeps its whole-number rule.
  it("still refuses a decimal", async () => {
    openLabelForEditing({
      id: "label-text",
      name: "Notes",
      type: "text",
      description: "",
      settings: { placeholder: "Your answer", minLength: 1, maxLength: 50 },
    });

    await typeValue("max", "50.5");

    await waitFor(() =>
      expect(underField("max").queryByText(INTEGER_SENTENCE)).not.toBeNull(),
    );
  });
});

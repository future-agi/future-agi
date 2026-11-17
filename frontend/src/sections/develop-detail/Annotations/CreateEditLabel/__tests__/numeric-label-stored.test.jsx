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

// The API sends a numeric label's settings in snake_case. Editing one in the
// project label form shows its stored Min, Max, Step Size and display, and
// Update sends them back as they are unless the person changes them. A label
// stored as buttons keeps that display in the select; a new label is offered
// the slider only. The reason a save is refused shows under Step Size and
// stays current.
const BUTTON_SCALE_SENTENCE =
  "A button scale can show up to 11 choices. Use a larger step or the slider.";

const PLACEHOLDERS = {
  name: "Enter label name",
  min: "Enter min value",
  max: "Enter max value",
  stepSize: "Enter stepsize",
};

const NEW_NAME = "Score (edited)";

const SLIDER_SETTINGS = {
  min: 0,
  max: 10,
  step_size: 1,
  display_type: "slider",
};

// 0 to 10 in steps of 2 draws 6 buttons.
const BUTTON_SETTINGS = {
  min: 0,
  max: 10,
  step_size: 2,
  display_type: "button",
};

// 0 to 20 in steps of 1 draws 21 buttons, more than a button scale shows.
const BUTTON_21_SETTINGS = {
  min: 0,
  max: 20,
  step_size: 1,
  display_type: "button",
};

// A numeric label as the API returns it.
const storedLabel = (settings) => ({
  id: "label-numeric",
  name: "Score",
  type: "numeric",
  description: "",
  settings,
});

let queryClient;

const field = (name) => screen.getByPlaceholderText(PLACEHOLDERS[name]);

// The text a number field shows under itself, as its helper text.
const underField = (name) =>
  within(field(name).closest(".MuiFormControl-root"));

// Any message a field shows under itself.
const messageUnder = (name) =>
  field(name)
    .closest(".MuiFormControl-root")
    .querySelector(".MuiFormHelperText-root");

const sentenceUnderStep = () =>
  underField("stepSize").queryByText(BUTTON_SCALE_SENTENCE);

const displaySelect = () =>
  within(
    screen.getByText("Display Options").closest(".MuiFormControl-root"),
  ).getByRole("combobox");

const updateButton = () =>
  screen.getByRole("button", { name: "save-edited-annotation" });

// Renders the real form; a null label opens it to create a new one.
const openLabelForm = (label) => {
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

const rename = async () => {
  await act(async () => {
    fireEvent.change(field("name"), { target: { value: NEW_NAME } });
  });
};

// Presses the real Update button once it is enabled.
const pressUpdate = async () => {
  await waitFor(() => expect(updateButton()).toBeEnabled());
  await act(async () => {
    fireEvent.click(updateButton());
  });
};

// Picks a display type in the Display Options select.
const chooseDisplay = async (name) => {
  await act(async () => {
    fireEvent.mouseDown(displaySelect());
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("option", { name }));
  });
};

// Opens the Display Options select and lists the choices it offers.
const displayChoices = async () => {
  await act(async () => {
    fireEvent.mouseDown(displaySelect());
  });
  return within(screen.getByRole("listbox"))
    .getAllByRole("option")
    .map((option) => option.textContent);
};

// Picks an annotation type in a new label's Annotation Type select.
const chooseType = async (name) => {
  await act(async () => {
    fireEvent.click(screen.getByPlaceholderText("Select Option"));
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("menuitem", { name }));
  });
};

const expectSaved = async (put, label, name, settings) => {
  await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
  expect(put).toHaveBeenCalledWith(endpoints.project.updateLabel(label.id), {
    name,
    type: "numeric",
    description: "",
    project: "project-numeric",
    settings,
  });
};

describe("a stored numeric label shown as a slider", () => {
  afterEach(() => {
    cleanup();
    queryClient?.clear();
    vi.restoreAllMocks();
  });

  it("opens with its stored Min, Max, Step Size and display", async () => {
    openLabelForm(storedLabel(SLIDER_SETTINGS));
    await settleValidation();

    expect(field("min")).toHaveValue(0);
    expect(field("max")).toHaveValue(10);
    expect(field("stepSize")).toHaveValue(1);
    expect(displaySelect()).toHaveTextContent("Slider");
    expect(messageUnder("stepSize")).toBeNull();
  });

  it("sends its stored settings when only the name changes", async () => {
    const label = storedLabel(SLIDER_SETTINGS);
    const { put, post } = openLabelForm(label);

    await rename();
    await pressUpdate();

    await expectSaved(put, label, NEW_NAME, {
      min: 0,
      max: 10,
      step_size: 1,
      display_type: "slider",
    });
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a stored numeric label shown as buttons", () => {
  afterEach(() => {
    cleanup();
    queryClient?.clear();
    vi.restoreAllMocks();
  });

  it("shows Buttons and sends it unchanged when the name changes", async () => {
    const label = storedLabel(BUTTON_SETTINGS);
    const { put, post } = openLabelForm(label);
    await settleValidation();

    expect(displaySelect()).toHaveTextContent("Buttons");
    expect(field("stepSize")).toHaveValue(2);
    expect(messageUnder("stepSize")).toBeNull();

    await rename();
    await pressUpdate();

    await expectSaved(put, label, NEW_NAME, {
      min: 0,
      max: 10,
      step_size: 2,
      display_type: "button",
    });
    expect(post).not.toHaveBeenCalled();
  });
});

// A button label saved earlier with more than 11 choices still opens, says
// why under Step Size, and is refused until the step or the display changes.
describe("a stored button label with 21 choices", () => {
  afterEach(() => {
    cleanup();
    queryClient?.clear();
    vi.restoreAllMocks();
  });

  it("opens with the reason under Step Size and refuses a save", async () => {
    const { put, post } = openLabelForm(storedLabel(BUTTON_21_SETTINGS));

    await waitFor(() => expect(sentenceUnderStep()).not.toBeNull());
    expect(field("stepSize")).toHaveValue(1);

    await submitForm();

    expect(sentenceUnderStep()).not.toBeNull();
    expect(put).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  it("drops the reason once Slider is picked and saves a slider", async () => {
    const label = storedLabel(BUTTON_21_SETTINGS);
    const { put, post } = openLabelForm(label);

    await waitFor(() => expect(sentenceUnderStep()).not.toBeNull());

    await chooseDisplay("Slider");

    await waitFor(() => expect(sentenceUnderStep()).toBeNull());
    await waitFor(() => expect(updateButton()).toBeEnabled());
    expect(field("name")).toHaveValue("Score");

    await act(async () => {
      fireEvent.click(updateButton());
    });

    await expectSaved(put, label, "Score", {
      min: 0,
      max: 20,
      step_size: 1,
      display_type: "slider",
    });
    expect(post).not.toHaveBeenCalled();
  });
});

describe("the display choices", () => {
  afterEach(() => {
    cleanup();
    queryClient?.clear();
    vi.restoreAllMocks();
  });

  it("offer a new numeric label the slider only", async () => {
    openLabelForm(null);

    await chooseType("Numeric");

    expect(await displayChoices()).toEqual(["Slider"]);
  });

  it("offer a stored slider label the slider only", async () => {
    openLabelForm(storedLabel(SLIDER_SETTINGS));

    expect(await displayChoices()).toEqual(["Slider"]);
  });

  it("offer a stored button label the slider and Buttons", async () => {
    openLabelForm(storedLabel(BUTTON_SETTINGS));

    expect(await displayChoices()).toEqual(["Slider", "Buttons"]);
  });
});

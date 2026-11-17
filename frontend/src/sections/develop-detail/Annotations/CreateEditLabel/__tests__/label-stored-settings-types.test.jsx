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

// The API sends a label's settings in snake_case. Editing a text, choice or
// star label in the project label form shows its stored settings, and Update
// sends them back as they are unless the person changes them. A numeric label
// stored as buttons keeps "Buttons" in its display choices while the form is
// open. A new label opens with today's defaults.
const PROJECT_ID = "project-labels";

const NEW_NAME = "Renamed label";

const PLACEHOLDERS = {
  name: "Enter label name",
  placeholderText: "Type here",
  min: "Enter min value",
  max: "Enter max value",
};

// Labels as the API returns them.
const STORED_TEXT = {
  id: "label-text",
  name: "Notes",
  type: "text",
  description: "",
  settings: {
    placeholder: "Write a short note",
    min_length: 2,
    max_length: 40,
  },
};

const STORED_CHOICE = {
  id: "label-choice",
  name: "Tone",
  type: "categorical",
  description: "",
  settings: {
    options: [{ label: "Calm" }, { label: "Tense" }],
    multi_choice: true,
    rule_prompt: "",
    auto_annotate: false,
    strategy: null,
  },
};

const STORED_STAR = {
  id: "label-star",
  name: "Rating",
  type: "star",
  description: "",
  settings: { no_of_stars: 7 },
};

// 0 to 10 in steps of 2 draws 6 buttons.
const STORED_BUTTONS = {
  id: "label-numeric",
  name: "Score",
  type: "numeric",
  description: "",
  settings: { min: 0, max: 10, step_size: 2, display_type: "button" },
};

let queryClient;

const field = (name) => screen.getByPlaceholderText(PLACEHOLDERS[name]);

const saveButton = () =>
  screen.getByRole("button", { name: "save-edited-annotation" });

// The Multi Choice box in the form, not the choice boxes in the preview.
const multiChoiceBox = () =>
  within(
    screen.getByText("Multi Choice").closest(".MuiFormControlLabel-root"),
  ).getByRole("checkbox");

// The stars the preview draws, named "1 Star", "2 Stars" and so on.
const previewStars = () =>
  screen.queryAllByRole("radio", { name: /^\d+ Stars?$/ });

const displaySelect = () =>
  within(
    screen.getByText("Display Options").closest(".MuiFormControl-root"),
  ).getByRole("combobox");

const labelForm = (label) => (
  <QueryClientProvider client={queryClient}>
    <SnackbarProvider>
      <CreateEditLabel
        open
        onClose={vi.fn()}
        projectId={PROJECT_ID}
        onSuccess={vi.fn()}
        editData={label}
      />
    </SnackbarProvider>
  </QueryClientProvider>
);

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
  const { rerender } = render(labelForm(label));
  return { put, post, rerender };
};

// The form checks the values on promises; one timer tick lets that check
// finish, so a value that is missing is really missing.
const settleValidation = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const typeName = async (name) => {
  await act(async () => {
    fireEvent.change(field("name"), { target: { value: name } });
  });
};

// Presses the real Update or Save button once it is enabled.
const pressSave = async () => {
  await waitFor(() => expect(saveButton()).toBeEnabled());
  await act(async () => {
    fireEvent.click(saveButton());
  });
};

// Picks a display type in the Display Options select and waits for the list
// to close.
const chooseDisplay = async (name) => {
  await act(async () => {
    fireEvent.mouseDown(displaySelect());
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("option", { name }));
  });
  await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
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

const expectUpdated = async (put, label, settings) => {
  await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
  expect(put).toHaveBeenCalledWith(endpoints.project.updateLabel(label.id), {
    name: NEW_NAME,
    type: label.type,
    description: "",
    project: PROJECT_ID,
    settings,
  });
};

afterEach(() => {
  cleanup();
  queryClient?.clear();
  vi.restoreAllMocks();
});

describe("a stored text label", () => {
  it("opens with its stored Min and Max", async () => {
    openLabelForm(STORED_TEXT);
    await settleValidation();

    expect(field("placeholderText")).toHaveValue("Write a short note");
    expect(field("min")).toHaveValue(2);
    expect(field("max")).toHaveValue(40);
  });

  it("sends its stored settings when only the name changes", async () => {
    const { put, post } = openLabelForm(STORED_TEXT);

    await typeName(NEW_NAME);
    await pressSave();

    await expectUpdated(put, STORED_TEXT, {
      placeholder: "Write a short note",
      min_length: 2,
      max_length: 40,
    });
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a stored choice label with multi choice on", () => {
  it("opens with Multi Choice on", async () => {
    openLabelForm(STORED_CHOICE);
    await settleValidation();

    expect(multiChoiceBox()).toBeChecked();
  });

  it("keeps Multi Choice on when only the name changes", async () => {
    const { put, post } = openLabelForm(STORED_CHOICE);

    await typeName(NEW_NAME);
    await pressSave();

    await expectUpdated(put, STORED_CHOICE, {
      options: [{ label: "Calm" }, { label: "Tense" }],
      multi_choice: true,
      rule_prompt: "",
      auto_annotate: false,
      strategy: null,
    });
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a stored star label with 7 stars", () => {
  it("opens showing 7 stars", async () => {
    openLabelForm(STORED_STAR);
    await settleValidation();

    expect(previewStars()).toHaveLength(7);
  });

  it("keeps 7 stars when only the name changes", async () => {
    const { put, post } = openLabelForm(STORED_STAR);

    await typeName(NEW_NAME);
    await pressSave();

    await expectUpdated(put, STORED_STAR, { no_of_stars: 7 });
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a stored numeric label shown as buttons", () => {
  it("still offers Buttons after Slider is picked", async () => {
    openLabelForm(STORED_BUTTONS);

    await chooseDisplay("Slider");

    expect(displaySelect()).toHaveTextContent("Slider");
    expect(await displayChoices()).toEqual(["Slider", "Buttons"]);
  });

  // Both callers clear the label they passed as the form closes, while it is
  // still on screen.
  it("still offers Buttons when the label is cleared", async () => {
    const { rerender } = openLabelForm(STORED_BUTTONS);

    rerender(labelForm(null));

    expect(await displayChoices()).toEqual(["Slider", "Buttons"]);
  });
});

describe("a new label", () => {
  it("opens a text label with empty Min and Max", async () => {
    openLabelForm(null);

    await chooseType("Text");

    expect(field("placeholderText")).toHaveValue("");
    expect(field("min")).toHaveValue(null);
    expect(field("max")).toHaveValue(null);
  });

  it("opens a choice label with Multi Choice off", async () => {
    openLabelForm(null);

    await chooseType("Categorical");

    expect(multiChoiceBox()).not.toBeChecked();
  });

  it("saves a star label with 5 stars", async () => {
    const { put, post } = openLabelForm(null);

    await chooseType("Star");
    await typeName("Rating");

    expect(previewStars()).toHaveLength(5);

    await pressSave();

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith(endpoints.project.createLabel(), {
      name: "Rating",
      type: "star",
      description: "",
      project: PROJECT_ID,
      settings: { no_of_stars: 5 },
    });
    expect(put).not.toHaveBeenCalled();
  });
});

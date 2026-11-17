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

// The API sends a text label's settings in snake_case. Editing a stored text
// label refuses a higher Min or a lower Max with the form's own sentence
// beside the field, and Update stays off. Max has no upper limit of its own,
// as the server sets none, so a label stored with a long Max opens and saves
// as it is. Min may be 0. A label stored with an empty placeholder opens and
// saves with it empty. A new text label meets the limits the server checks
// and still needs a placeholder.
const MIN_RAISED_SENTENCE =
  "Min cannot be increased while editing — existing annotations may be affected.";
const MAX_LOWERED_SENTENCE =
  "Max cannot be decreased while editing — existing annotations may be affected.";
const MAX_CAP_SENTENCE = "Max length cannot exceed 255 characters";
const MIN_ABOVE_MAX_SENTENCE = "Min length cannot be greater than max length";
const MIN_BELOW_ZERO_SENTENCE = "Value must be positive or zero";
const PLACEHOLDER_SENTENCE = "Placeholder is required";

const PROJECT_ID = "project-text";

const NEW_NAME = "Notes (edited)";

const PLACEHOLDERS = {
  name: "Enter label name",
  placeholderText: "Type here",
  min: "Enter min value",
  max: "Enter max value",
};

// A text label as the API returns it.
const storedTextLabel = (settings) => ({
  id: "label-text",
  name: "Notes",
  type: "text",
  description: "",
  settings: { placeholder: "Write a short note", ...settings },
});

const SHORT_LIMITS = { min_length: 2, max_length: 40 };

const LONG_MAX = { min_length: 1, max_length: 500 };

const MIN_ZERO = { min_length: 0, max_length: 500 };

const MIN_BELOW_ZERO = { min_length: -1, max_length: 40 };

const EMPTY_PLACEHOLDER = { placeholder: "", min_length: 2, max_length: 40 };

// The settings the other label form gives a new text label.
const OTHER_FORM_DEFAULTS = {
  placeholder: "",
  min_length: 0,
  max_length: 500,
};

let queryClient;

const field = (name) => screen.getByPlaceholderText(PLACEHOLDERS[name]);

// The text a field shows under itself, as its helper text.
const underField = (name) =>
  within(field(name).closest(".MuiFormControl-root"));

// Any message a field shows under itself.
const messageUnder = (name) =>
  field(name)
    .closest(".MuiFormControl-root")
    .querySelector(".MuiFormHelperText-root");

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
          projectId={PROJECT_ID}
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

const typeValue = async (name, value) => {
  await act(async () => {
    fireEvent.change(field(name), { target: { value } });
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

// Presses the real Update or Save button once it is enabled.
const pressUpdate = async () => {
  await waitFor(() => expect(updateButton()).toBeEnabled());
  await act(async () => {
    fireEvent.click(updateButton());
  });
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

const expectUpdated = async (put, label, name, settings) => {
  await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
  expect(put).toHaveBeenCalledWith(endpoints.project.updateLabel(label.id), {
    name,
    type: "text",
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

describe("a stored text label with Min 2 and Max 40", () => {
  it("refuses a higher Min with the form's sentence beside Min", async () => {
    const { put, post } = openLabelForm(storedTextLabel(SHORT_LIMITS));

    await typeValue("min", "5");

    expect(field("min")).toHaveValue(5);
    await waitFor(() =>
      expect(underField("min").queryByText(MIN_RAISED_SENTENCE)).not.toBeNull(),
    );
    expect(updateButton()).toBeDisabled();

    await submitForm();

    expect(underField("min").queryByText(MIN_RAISED_SENTENCE)).not.toBeNull();
    expect(put).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  it("refuses a lower Max with the form's sentence beside Max", async () => {
    const { put, post } = openLabelForm(storedTextLabel(SHORT_LIMITS));

    await typeValue("max", "30");

    expect(field("max")).toHaveValue(30);
    await waitFor(() =>
      expect(
        underField("max").queryByText(MAX_LOWERED_SENTENCE),
      ).not.toBeNull(),
    );
    expect(updateButton()).toBeDisabled();

    await submitForm();

    expect(underField("max").queryByText(MAX_LOWERED_SENTENCE)).not.toBeNull();
    expect(put).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  it("accepts a higher Max and sends it", async () => {
    const label = storedTextLabel(SHORT_LIMITS);
    const { put, post } = openLabelForm(label);

    await typeValue("max", "60");
    await settleValidation();

    expect(messageUnder("max")).toBeNull();

    await pressUpdate();

    await expectUpdated(put, label, "Notes", {
      placeholder: "Write a short note",
      min_length: 2,
      max_length: 60,
    });
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a stored text label with Max 500", () => {
  it("can be updated after a rename and sends Max 500", async () => {
    const label = storedTextLabel(LONG_MAX);
    const { put, post } = openLabelForm(label);

    await typeValue("name", NEW_NAME);
    await pressUpdate();

    await expectUpdated(put, label, NEW_NAME, {
      placeholder: "Write a short note",
      min_length: 1,
      max_length: 500,
    });
    expect(post).not.toHaveBeenCalled();
  });

  it("shows no upper limit under Max and sends it as stored", async () => {
    const label = storedTextLabel(LONG_MAX);
    const { put, post } = openLabelForm(label);

    await submitForm();

    expect(underField("max").queryByText(MAX_CAP_SENTENCE)).toBeNull();
    expect(messageUnder("max")).toBeNull();
    await expectUpdated(put, label, "Notes", {
      placeholder: "Write a short note",
      min_length: 1,
      max_length: 500,
    });
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a stored text label with Min 0", () => {
  it("can be updated after a rename and sends Min 0", async () => {
    const label = storedTextLabel(MIN_ZERO);
    const { put, post } = openLabelForm(label);

    await typeValue("name", NEW_NAME);
    await pressUpdate();

    await expectUpdated(put, label, NEW_NAME, {
      placeholder: "Write a short note",
      min_length: 0,
      max_length: 500,
    });
    expect(post).not.toHaveBeenCalled();
  });
});

// Min drops a typed minus, so a Min below 0 only arrives with a stored label.
describe("a stored text label with Min -1", () => {
  it("shows the form's sentence under Min and sends nothing", async () => {
    const { put, post } = openLabelForm(storedTextLabel(MIN_BELOW_ZERO));

    await submitForm();

    expect(field("min")).toHaveValue(-1);
    expect(
      underField("min").queryByText(MIN_BELOW_ZERO_SENTENCE),
    ).not.toBeNull();
    expect(put).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });
});

// A Min stored below 0 can be raised to 0, the lowest Min the form takes, so
// the label can still be saved; a Min above 0 is refused as for any label.
describe("a stored text label with Min -1 when edited", () => {
  it("can be renamed with Min 0 and sends Min 0", async () => {
    const label = storedTextLabel(MIN_BELOW_ZERO);
    const { put, post } = openLabelForm(label);

    await typeValue("min", "0");
    await typeValue("name", NEW_NAME);

    expect(field("min")).toHaveValue(0);
    await pressUpdate();

    await expectUpdated(put, label, NEW_NAME, {
      placeholder: "Write a short note",
      min_length: 0,
      max_length: 40,
    });
    expect(post).not.toHaveBeenCalled();
  });

  it("still refuses Min 1 with the form's sentence beside Min", async () => {
    const { put, post } = openLabelForm(storedTextLabel(MIN_BELOW_ZERO));

    await typeValue("min", "1");

    expect(field("min")).toHaveValue(1);
    await waitFor(() =>
      expect(underField("min").queryByText(MIN_RAISED_SENTENCE)).not.toBeNull(),
    );
    expect(updateButton()).toBeDisabled();

    await submitForm();

    expect(underField("min").queryByText(MIN_RAISED_SENTENCE)).not.toBeNull();
    expect(put).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a stored text label with an empty placeholder", () => {
  it("can be updated after a rename and sends it empty", async () => {
    const label = storedTextLabel(EMPTY_PLACEHOLDER);
    const { put, post } = openLabelForm(label);

    expect(field("placeholderText")).toHaveValue("");
    expect(field("placeholderText")).not.toBeRequired();

    await typeValue("name", NEW_NAME);
    await pressUpdate();

    await expectUpdated(put, label, NEW_NAME, {
      placeholder: "",
      min_length: 2,
      max_length: 40,
    });
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a stored text label with the other label form's defaults", () => {
  it("can be updated after a rename and sends them as stored", async () => {
    const label = storedTextLabel(OTHER_FORM_DEFAULTS);
    const { put, post } = openLabelForm(label);

    await typeValue("name", NEW_NAME);
    await pressUpdate();

    await expectUpdated(put, label, NEW_NAME, {
      placeholder: "",
      min_length: 0,
      max_length: 500,
    });
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a stored text label with a placeholder", () => {
  it("still needs one when it is cleared", async () => {
    const { put, post } = openLabelForm(storedTextLabel(SHORT_LIMITS));

    expect(field("placeholderText")).toBeRequired();

    await typeValue("placeholderText", "");

    await waitFor(() =>
      expect(
        underField("placeholderText").queryByText(PLACEHOLDER_SENTENCE),
      ).not.toBeNull(),
    );
    expect(updateButton()).toBeDisabled();

    await submitForm();

    expect(put).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a new text label", () => {
  it("still needs a placeholder", async () => {
    const { put, post } = openLabelForm(null);

    await chooseType("Text");
    await typeValue("name", "Notes");
    await typeValue("min", "1");
    await typeValue("max", "40");

    expect(field("placeholderText")).toBeRequired();

    await submitForm();

    expect(
      underField("placeholderText").queryByText(PLACEHOLDER_SENTENCE),
    ).not.toBeNull();
    expect(put).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  it("saves a Max above 255 as typed", async () => {
    const { put, post } = openLabelForm(null);

    await chooseType("Text");
    await typeValue("name", "Notes");
    await typeValue("placeholderText", "Your answer");
    await typeValue("min", "1");
    await typeValue("max", "300");
    await settleValidation();

    expect(messageUnder("max")).toBeNull();

    await pressUpdate();

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith(endpoints.project.createLabel(), {
      name: "Notes",
      type: "text",
      description: "",
      project: PROJECT_ID,
      settings: { placeholder: "Your answer", min_length: 1, max_length: 300 },
    });
    expect(put).not.toHaveBeenCalled();
  });

  it("still refuses a Min above Max in the form's words", async () => {
    const { put, post } = openLabelForm(null);

    await chooseType("Text");
    await typeValue("name", "Notes");
    await typeValue("placeholderText", "Your answer");
    await typeValue("max", "5");
    await typeValue("min", "10");

    await waitFor(() =>
      expect(
        underField("min").queryByText(MIN_ABOVE_MAX_SENTENCE),
      ).not.toBeNull(),
    );

    await submitForm();

    expect(
      underField("min").queryByText(MIN_ABOVE_MAX_SENTENCE),
    ).not.toBeNull();
    expect(put).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });
});

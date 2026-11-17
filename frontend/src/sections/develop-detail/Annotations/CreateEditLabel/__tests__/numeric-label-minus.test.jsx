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

// A minus typed in a numeric label's Min or Max stays in the field, the
// form's own sentence says why the value is refused, and nothing is sent.
// Step Size still drops a typed minus, as before. Both numeric label forms
// refuse a bad Min, Max or Step Size in these same words.
const MIN_SENTENCE = "Enter a value of 0 or more";
const MAX_SENTENCE = "Enter a value above 0";
const STEP_SENTENCE = "Enter a value above 0";
// Other forms that share the number check keep its own words.
const OTHER_FORM_SENTENCE = "Value must be positive";

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

describe("a minus typed in a numeric label's Min or Max", () => {
  afterEach(() => {
    cleanup();
    queryClient?.clear();
    vi.restoreAllMocks();
  });

  // Saved min 8: a dropped minus leaves 5, which this label would accept and
  // send (not above the saved min, below the max), so only the field decides.
  it("stays in Min, shows the form's sentence and sends nothing", async () => {
    const { put, post } = openLabelForEditing(
      numericLabel({ min: 8, max: 10, stepSize: 1 }),
    );

    await typeValue("min", "-5");

    expect(field("min")).toHaveValue(-5);
    await waitFor(() =>
      expect(underField("min").queryByText(MIN_SENTENCE)).not.toBeNull(),
    );

    await submitForm();

    expect(field("min")).toHaveValue(-5);
    expect(underField("min").queryByText(MIN_SENTENCE)).not.toBeNull();
    expect(put).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  // Saved max 3 and step 1: a dropped minus leaves 5, which this label would
  // accept and send (not below the saved max or the step), so only the field
  // decides.
  it("stays in Max, shows the form's sentence and sends nothing", async () => {
    const { put, post } = openLabelForEditing(
      numericLabel({ min: 0, max: 3, stepSize: 1 }),
    );

    await typeValue("max", "-5");

    expect(field("max")).toHaveValue(-5);
    await waitFor(() =>
      expect(underField("max").queryByText(MAX_SENTENCE)).not.toBeNull(),
    );

    await submitForm();

    expect(field("max")).toHaveValue(-5);
    expect(underField("max").queryByText(MAX_SENTENCE)).not.toBeNull();
    expect(put).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a numeric label's Step Size and a valid save", () => {
  afterEach(() => {
    cleanup();
    queryClient?.clear();
    vi.restoreAllMocks();
  });

  it("still drops a minus typed in Step Size, as before", async () => {
    openLabelForEditing(numericLabel({ min: 0, max: 10, stepSize: 1 }));

    await typeValue("stepSize", "-2");
    await settleValidation();

    expect(field("stepSize")).toHaveValue(2);
    expect(underField("stepSize").queryByText(STEP_SENTENCE)).toBeNull();
  });

  it("still saves a valid 0 to 10 label with the same payload", async () => {
    const label = numericLabel({ min: 0, max: 8, stepSize: 1 });
    const { put, post } = openLabelForEditing(label);

    await typeValue("max", "10");

    const update = screen.getByRole("button", {
      name: "save-edited-annotation",
    });
    await waitFor(() => expect(update).toBeEnabled());
    await act(async () => {
      fireEvent.click(update);
    });

    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    expect(put).toHaveBeenCalledWith(endpoints.project.updateLabel(label.id), {
      name: "Score",
      type: "numeric",
      description: "",
      project: "project-numeric",
      settings: { min: 0, max: 10, step_size: 1, display_type: "slider" },
    });
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a numeric label's Max or Step Size of 0", () => {
  afterEach(() => {
    cleanup();
    queryClient?.clear();
    vi.restoreAllMocks();
  });

  it("in Max shows the same words and sends nothing", async () => {
    const { put, post } = openLabelForEditing(
      numericLabel({ min: 0, max: 3, stepSize: 1 }),
    );

    await typeValue("max", "0");

    expect(field("max")).toHaveValue(0);
    await waitFor(() =>
      expect(underField("max").queryByText(MAX_SENTENCE)).not.toBeNull(),
    );

    await submitForm();

    expect(underField("max").queryByText(MAX_SENTENCE)).not.toBeNull();
    expect(put).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  it("in Step Size shows the same words and sends nothing", async () => {
    const { put, post } = openLabelForEditing(
      numericLabel({ min: 0, max: 10, stepSize: 1 }),
    );

    await typeValue("stepSize", "0");

    expect(field("stepSize")).toHaveValue(0);
    await waitFor(() =>
      expect(underField("stepSize").queryByText(STEP_SENTENCE)).not.toBeNull(),
    );

    await submitForm();

    expect(underField("stepSize").queryByText(STEP_SENTENCE)).not.toBeNull();
    expect(put).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a text label's Max of 0", () => {
  afterEach(() => {
    cleanup();
    queryClient?.clear();
    vi.restoreAllMocks();
  });

  // The text label form shares the number check with the numeric label form
  // and keeps the check's own words.
  it("is still refused with the shared check's words", async () => {
    openLabelForEditing({
      id: "label-text",
      name: "Notes",
      type: "text",
      description: "",
      settings: { placeholder: "Your answer", minLength: 1, maxLength: 50 },
    });

    await typeValue("max", "0");

    await waitFor(() =>
      expect(underField("max").queryByText(OTHER_FORM_SENTENCE)).not.toBeNull(),
    );
    expect(underField("max").queryByText(MAX_SENTENCE)).toBeNull();
  });
});

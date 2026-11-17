import React from "react";
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
import LabelModal from "../LabelModal";

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-1" }),
}));

vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: () => {},
  Events: {},
  PropertyName: {},
}));

// The label form of an annotation view is the third numeric label form. It
// refuses a bad Min, Max or Step Size in the same words as the other two, a
// Min of 0 is a value, and a field's sign comes before any comparison with
// another field. Decimals are still allowed.
const MIN_SENTENCE = "Enter a value of 0 or more";
const MAX_SENTENCE = "Enter a value above 0";
const STEP_SENTENCE = "Enter a value above 0";
// The words this form keeps.
const MIN_REQUIRED = "Min value is required";
const MAX_ABOVE_MIN = "Max value must be greater than Min";
const STEP_RANGE = "Step size must lie between 0 and (Max - Min)";
// A text label's length rules are not numeric labels and keep their words.
const TEXT_MIN_SENTENCE = "Min value must be a non-negative number";

const PLACEHOLDERS = {
  name: "Type here...",
  description: "Enter description here",
  min: "Enter min value",
  max: "Enter max value",
  stepSize: "Enter stepsize",
};

const field = (name) => screen.getByPlaceholderText(PLACEHOLDERS[name]);

// The text a field shows under itself, as its helper text.
const underField = (name) =>
  within(field(name).closest(".MuiFormControl-root"));

const openLabelModal = () => {
  const handleLabels = vi.fn();
  render(
    <LabelModal
      open
      onClose={vi.fn()}
      columnData={[{ staticFieldColumn: [] }]}
      modalLabel="Add label"
      handleLabels={handleLabels}
    />,
  );
  return { handleLabels };
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
// is sent, not the Save button being disabled.
const submitForm = async () => {
  await act(async () => {
    fireEvent.submit(field("min").closest("form"));
  });
  await settleValidation();
};

// Picks an option in one of the form's selects. A select's label shows twice
// (over the field and in the outline's gap); both sit in the same field.
const choose = async (selectLabel, name) => {
  const select = within(
    screen.getAllByText(selectLabel)[0].closest(".MuiFormControl-root"),
  ).getByRole("combobox");
  await act(async () => {
    fireEvent.mouseDown(select);
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("option", { name }));
  });
};

// A name, a description and the slider: all a numeric label needs besides
// its numbers. Step Size shows once the slider is chosen.
const startNumericLabel = async () => {
  await typeValue("name", "Score");
  await typeValue("description", "How good the answer is");
  await choose("Display Options", "Slider");
};

const fillNumbers = async ({ min, max, stepSize }) => {
  await typeValue("min", min);
  await typeValue("max", max);
  await typeValue("stepSize", stepSize);
};

const numericPayload = (settings) => ({
  name: "Score",
  type: "numeric",
  organization: "org-1",
  settings: { display_type: "slider", ...settings },
});

describe("the annotation view's numeric label form", () => {
  afterEach(() => {
    cleanup();
  });

  it("saves a Min of 0", async () => {
    const { handleLabels } = openLabelModal();

    await startNumericLabel();
    await fillNumbers({ min: "0", max: "5", stepSize: "1" });
    await submitForm();

    expect(underField("min").queryByText(MIN_REQUIRED)).toBeNull();
    await waitFor(() => expect(handleLabels).toHaveBeenCalledTimes(1));
    expect(handleLabels).toHaveBeenCalledWith(
      numericPayload({ min: 0, max: 5, step_size: 1 }),
    );
  });

  it("refuses a Min of -1 in the shared words and sends nothing", async () => {
    const { handleLabels } = openLabelModal();

    await startNumericLabel();
    await fillNumbers({ min: "-1", max: "5", stepSize: "1" });
    await submitForm();

    await waitFor(() =>
      expect(underField("min").queryByText(MIN_SENTENCE)).not.toBeNull(),
    );
    expect(handleLabels).not.toHaveBeenCalled();
  });

  it("refuses a Max of 0 under Max before comparing it with Min", async () => {
    const { handleLabels } = openLabelModal();

    await startNumericLabel();
    await fillNumbers({ min: "0", max: "0", stepSize: "1" });
    await submitForm();

    await waitFor(() =>
      expect(underField("max").queryByText(MAX_SENTENCE)).not.toBeNull(),
    );
    expect(underField("max").queryByText(MAX_ABOVE_MIN)).toBeNull();
    expect(handleLabels).not.toHaveBeenCalled();
  });

  it("refuses a Step Size of 0 under Step Size", async () => {
    const { handleLabels } = openLabelModal();

    await startNumericLabel();
    await fillNumbers({ min: "0", max: "5", stepSize: "0" });
    await submitForm();

    await waitFor(() =>
      expect(underField("stepSize").queryByText(STEP_SENTENCE)).not.toBeNull(),
    );
    expect(underField("stepSize").queryByText(STEP_RANGE)).toBeNull();
    expect(handleLabels).not.toHaveBeenCalled();
  });

  // A guard: decimals were already accepted, and still are.
  it("saves Min 0.5, Max 2.5 and Step Size 0.5", async () => {
    const { handleLabels } = openLabelModal();

    await startNumericLabel();
    await fillNumbers({ min: "0.5", max: "2.5", stepSize: "0.5" });
    await submitForm();

    await waitFor(() => expect(handleLabels).toHaveBeenCalledTimes(1));
    expect(handleLabels).toHaveBeenCalledWith(
      numericPayload({ min: 0.5, max: 2.5, step_size: 0.5 }),
    );
  });
});

// Guards: these words were there before the change and stay.
describe("the annotation view's label form keeps its other words", () => {
  afterEach(() => {
    cleanup();
  });

  it("still asks for an empty Min", async () => {
    const { handleLabels } = openLabelModal();

    await startNumericLabel();
    await typeValue("max", "5");
    await typeValue("stepSize", "1");
    await submitForm();

    await waitFor(() =>
      expect(underField("min").queryByText(MIN_REQUIRED)).not.toBeNull(),
    );
    expect(handleLabels).not.toHaveBeenCalled();
  });

  it("still refuses a Max of 3 with a Min of 5", async () => {
    const { handleLabels } = openLabelModal();

    await startNumericLabel();
    await fillNumbers({ min: "5", max: "3", stepSize: "1" });
    await submitForm();

    await waitFor(() =>
      expect(underField("max").queryByText(MAX_ABOVE_MIN)).not.toBeNull(),
    );
    expect(handleLabels).not.toHaveBeenCalled();
  });

  it("still refuses a Step Size of 10 between 0 and 5", async () => {
    const { handleLabels } = openLabelModal();

    await startNumericLabel();
    await fillNumbers({ min: "0", max: "5", stepSize: "10" });
    await submitForm();

    await waitFor(() =>
      expect(underField("stepSize").queryByText(STEP_RANGE)).not.toBeNull(),
    );
    expect(handleLabels).not.toHaveBeenCalled();
  });

  it("keeps a text label's own Min and Max words", async () => {
    const { handleLabels } = openLabelModal();

    await typeValue("name", "Notes");
    await typeValue("description", "Free text");
    await choose("Annotation Type", "Text");
    await typeValue("min", "-1");
    await typeValue("max", "-2");
    await submitForm();

    await waitFor(() =>
      expect(underField("min").queryByText(TEXT_MIN_SENTENCE)).not.toBeNull(),
    );
    expect(underField("max").queryByText(MAX_ABOVE_MIN)).not.toBeNull();
    expect(underField("min").queryByText(MIN_SENTENCE)).toBeNull();
    expect(underField("max").queryByText(MAX_SENTENCE)).toBeNull();
    expect(handleLabels).not.toHaveBeenCalled();
  });
});

// The number field in an annotate form offers only the numbers its label's
// limits allow. A label with max 0 stops + and the slider at 0, as the server
// refuses anything above it. A label with no max has no top on the server, so
// + goes on past 100 and the slider shows the number as it is.
import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it, vi } from "vitest";
import { useForm } from "react-hook-form";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import NumericLabelField from "../LabelFields/NumericLabelField";

// Steps of 1 from -5 up to a max of 0: the server takes -5 to 0.
const MAX_ZERO = { min: -5, max: 0, step_size: 1, display_type: "slider" };

// No max: the server takes any whole number from 0.
const NO_MAX = { min: 0, step_size: 1, display_type: "slider" };
const NULL_MAX = { min: 0, max: null, step_size: 1, display_type: "slider" };

function NumberForm({ settings, value, onSave }) {
  const { control, handleSubmit } = useForm({
    defaultValues: { score: value },
  });
  return (
    <>
      <NumericLabelField
        control={control}
        fieldName="score"
        label="Score"
        settings={settings}
      />
      <button type="button" onClick={handleSubmit(onSave)}>
        Save
      </button>
    </>
  );
}

NumberForm.propTypes = {
  settings: PropTypes.object.isRequired,
  value: PropTypes.number,
  onSave: PropTypes.func.isRequired,
};

// The − and + buttons are the two buttons beside the slider.
const stepButtons = () => {
  let box = screen.getByRole("slider");
  while (box && box.querySelectorAll("button").length < 2) {
    box = box.parentElement;
  }
  const [minus, plus] = box.querySelectorAll("button");
  return { minus, plus };
};

// The number shown between − and +.
const shownNumber = () => stepButtons().minus.nextElementSibling.textContent;

const press = async (button) => {
  await act(async () => {
    fireEvent.click(button);
  });
};

const openForm = (settings, value) => {
  const onSave = vi.fn();
  render(<NumberForm settings={settings} value={value} onSave={onSave} />);
  return onSave;
};

// Saves the form and gives the number it saved.
const savedNumber = async (onSave) => {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
  });
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  return onSave.mock.calls[0][0].score;
};

// The number between − and + and the number the slider reports agree.
const expectShown = (value) => {
  expect(shownNumber()).toBe(String(value));
  expect(screen.getByRole("slider")).toHaveAttribute(
    "aria-valuenow",
    String(value),
  );
};

describe("annotate form number field: a label with max 0", () => {
  it("+ is off at 0, a press changes nothing and the slider ends at 0", async () => {
    const onSave = openForm(MAX_ZERO, 0);

    expect(stepButtons().plus).toBeDisabled();
    expect(screen.getByRole("slider")).toHaveAttribute("max", "0");
    await press(stepButtons().plus);

    expectShown(0);
    expect(await savedNumber(onSave)).toBe(0);
  });

  it("− still steps down from 0", async () => {
    const onSave = openForm(MAX_ZERO, 0);

    await press(stepButtons().minus);

    expectShown(-1);
    expect(await savedNumber(onSave)).toBe(-1);
  });
});

describe("annotate form number field: a label with no max", () => {
  it.each([
    ["no max at all", NO_MAX],
    ["a max of null", NULL_MAX],
  ])("with %s, + goes past 100", async (_, settings) => {
    const onSave = openForm(settings, 100);

    expect(stepButtons().plus).not.toBeDisabled();
    await press(stepButtons().plus);
    await press(stepButtons().plus);

    expectShown(102);
    expect(await savedNumber(onSave)).toBe(102);
  });

  it("a stored number past 100 shows on the slider as it is", async () => {
    const onSave = openForm(NO_MAX, 250);

    expectShown(250);
    await press(stepButtons().plus);

    expectShown(251);
    expect(await savedNumber(onSave)).toBe(251);
  });

  it("the slider shows 0 to 100 while the number is within them", async () => {
    openForm(NO_MAX, 40);
    const slider = screen.getByRole("slider");

    expect(slider).toHaveAttribute("min", "0");
    expect(slider).toHaveAttribute("max", "100");
    expectShown(40);
  });
});

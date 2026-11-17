// The − and + buttons beside a number field's slider have names a screen
// reader reads: "Decrease" and "Increase". Each name is on the button that
// does it, and an off button keeps its name.
import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it } from "vitest";
import { useForm } from "react-hook-form";
import { act, fireEvent, render, screen } from "src/utils/test-utils";
import NumericLabelField from "../LabelFields/NumericLabelField";

// A number label stepping by 2 from 1: the server accepts 1, 3, 5, 7 and 9.
const ODD_STEPS = { min: 1, max: 9, step_size: 2, display_type: "slider" };

function NumberField({ value }) {
  const { control } = useForm({ defaultValues: { score: value } });
  return (
    <NumericLabelField
      control={control}
      fieldName="score"
      label="Score"
      settings={ODD_STEPS}
    />
  );
}

NumberField.propTypes = {
  value: PropTypes.number.isRequired,
};

const press = async (button) => {
  await act(async () => {
    fireEvent.click(button);
  });
};

const expectValue = (value) =>
  expect(screen.getByRole("slider")).toHaveAttribute(
    "aria-valuenow",
    String(value),
  );

describe("annotate form number field: the − and + button names", () => {
  it("has a Decrease and an Increase button", () => {
    render(<NumberField value={3} />);

    expect(screen.getByRole("button", { name: "Decrease" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Increase" })).toBeEnabled();
  });

  it("Increase steps up and Decrease steps down", async () => {
    render(<NumberField value={3} />);

    await press(screen.getByRole("button", { name: "Increase" }));
    expectValue(5);

    await press(screen.getByRole("button", { name: "Decrease" }));
    await press(screen.getByRole("button", { name: "Decrease" }));
    expectValue(1);
  });

  it("an off button keeps its name", () => {
    render(<NumberField value={9} />);

    expect(screen.getByRole("button", { name: "Increase" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Decrease" })).toBeEnabled();
  });
});

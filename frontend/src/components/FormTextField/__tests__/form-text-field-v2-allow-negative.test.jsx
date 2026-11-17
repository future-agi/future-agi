import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import { handleNumericInput } from "../../ComplexFilter/common";
import FormTextFieldV2 from "../FormTextFieldV2";

// A number field drops every "-" unless it opts in with allowNegative, which
// keeps one leading "-" and nothing else.
describe("handleNumericInput", () => {
  it.each([
    ["-0.1", "-0.1"],
    ["--5", "-5"],
    ["5-3", "53"],
    ["-5-3", "-53"],
    ["-1.2.3", "-1.23"],
    ["-", "-"],
    ["-.", "-."],
    ["0.2", "0.2"],
  ])("with allowNegative turns %j into %j", (typed, kept) => {
    expect(handleNumericInput(typed, { allowNegative: true })).toBe(kept);
  });

  it.each([
    ["-0.1", "0.1"],
    ["--5", "5"],
    ["5-3", "53"],
    ["-", ""],
    ["1.2.3", "1.23"],
    ["1..2", "1.2"],
    [".5", ".5"],
    ["abc12", "12"],
    ["", ""],
  ])("without it still turns %j into %j", (typed, kept) => {
    expect(handleNumericInput(typed)).toBe(kept);
    expect(handleNumericInput(typed, { allowNegative: false })).toBe(kept);
  });
});

// A form with one number field; it prints what the form holds for it.
function AmountForm({ allowNegative }) {
  const { control, watch } = useForm({ defaultValues: { amount: 0.5 } });
  const amount = watch("amount");
  return (
    <>
      <FormTextFieldV2
        control={control}
        fieldName="amount"
        label="Amount"
        fieldType="number"
        inputProps={{ "data-testid": "amount" }}
        {...(allowNegative !== undefined && { allowNegative })}
      />
      <output data-testid="form-amount">{`${typeof amount}:${amount}`}</output>
    </>
  );
}

AmountForm.propTypes = {
  allowNegative: PropTypes.bool,
};

const typeAmount = async (value) => {
  await act(async () => {
    fireEvent.change(screen.getByTestId("amount"), { target: { value } });
  });
};

describe("FormTextFieldV2 allowNegative", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  // First render with the prop in this file: React warns once per unknown
  // prop name, so a later render would not show the warning.
  it("keeps a typed minus and never hands the prop to the page", async () => {
    const consoleError = vi.spyOn(console, "error");
    render(<AmountForm allowNegative />);

    await typeAmount("-0.1");

    expect(screen.getByTestId("amount")).toHaveValue(-0.1);
    expect(screen.getByTestId("form-amount")).toHaveTextContent("number:-0.1");
    const mentionsProp = consoleError.mock.calls.some((args) =>
      args.some((arg) => String(arg).includes("allowNegative")),
    );
    expect(mentionsProp).toBe(false);
    expect(document.querySelector("[allownegative]")).toBeNull();
  });

  it("hands the form an empty value, not NaN, while the number is half typed", async () => {
    render(<AmountForm allowNegative />);

    // A number field reports "" while it holds "-", "-." or "-0.".
    for (const step of ["-", "-0", "-0.", "-0.3"]) {
      await typeAmount(step);
      expect(screen.getByTestId("form-amount")).not.toHaveTextContent("NaN");
    }

    expect(screen.getByTestId("amount")).toHaveValue(-0.3);
    expect(screen.getByTestId("form-amount")).toHaveTextContent("number:-0.3");
  });

  it("drops the minus without it, as before", async () => {
    render(<AmountForm />);

    await typeAmount("-0.1");

    expect(screen.getByTestId("amount")).toHaveValue(0.1);
    expect(screen.getByTestId("form-amount")).toHaveTextContent("number:0.1");
  });
});

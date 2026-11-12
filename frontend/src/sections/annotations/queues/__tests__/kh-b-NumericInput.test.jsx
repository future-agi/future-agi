// KH-SCOPE §6 B1, mount M-B3 (ships in kh-review/integration.patch with the
// mount): LabelInput's numeric field shows the rule, refuses inline, keeps
// the previous value and never clamps on blur.
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import LabelInput from "../annotate/label-input";

const HELP = {
  name: "Helpfulness",
  type: "numeric",
  settings: { min: 0, max: 1, step_size: 0.1, display_type: "slider" },
};

describe("LabelInput numeric (KH B1)", () => {
  it("shows the rule under the field", () => {
    render(
      <LabelInput label={HELP} value={{ value: 0.5 }} onChange={vi.fn()} />,
    );
    expect(screen.getByTestId("numeric-rule")).toHaveTextContent(
      "0 – 1 · steps of 0.1",
    );
  });

  it("refuses 1.4 inline and keeps the previous value", () => {
    const onChange = vi.fn();
    render(
      <LabelInput label={HELP} value={{ value: 0.5 }} onChange={onChange} />,
    );
    const field = screen.getByRole("spinbutton", { name: "Helpfulness" });
    fireEvent.change(field, { target: { value: "1.4" } });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Helpfulness must be between 0 and 1 (you entered 1.4).",
    );
    expect(onChange).not.toHaveBeenCalled();
  });

  it("-0.2 then blur: the reason, no clamp to the minimum", () => {
    const onChange = vi.fn();
    render(
      <LabelInput label={HELP} value={{ value: 0.5 }} onChange={onChange} />,
    );
    const field = screen.getByRole("spinbutton", { name: "Helpfulness" });
    fireEvent.change(field, { target: { value: "-0.2" } });
    fireEvent.blur(field);
    expect(onChange).not.toHaveBeenCalled();
    expect(field).toHaveValue(0.5);
    expect(screen.getByRole("alert")).toHaveTextContent("(you entered -0.2)");
  });

  it("an empty score shows an empty field, not the minimum", () => {
    render(<LabelInput label={HELP} value={null} onChange={vi.fn()} />);
    expect(screen.getByRole("spinbutton", { name: "Helpfulness" })).toHaveValue(
      null,
    );
  });

  it("button display: the same rule and refusal", () => {
    const onChange = vi.fn();
    render(
      <LabelInput
        label={{
          ...HELP,
          settings: {
            ...HELP.settings,
            display_type: "button",
            step_size: 0.5,
          },
        }}
        value={{ value: 0.5 }}
        onChange={onChange}
      />,
    );
    expect(screen.getByTestId("numeric-rule")).toHaveTextContent(
      "0 – 1 · steps of 0.5",
    );
    fireEvent.change(screen.getByRole("spinbutton"), {
      target: { value: "0.7" },
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Helpfulness moves in steps of 0.5 (you entered 0.7).",
    );
    expect(onChange).not.toHaveBeenCalled();
  });
});

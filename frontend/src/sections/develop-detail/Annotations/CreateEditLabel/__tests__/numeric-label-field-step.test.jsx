// The number field in an annotate form (the trace annotate drawer and the
// other annotate drawers) steps the way the server checks a number label: by
// the label's step_size as the API sends it, or stepSize when that is all it
// has, counted from the label's minimum (0 when it has none). So an arrow
// press only reaches values the server accepts.
import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it } from "vitest";
import { useForm } from "react-hook-form";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import NumericLabelField from "../LabelFields/NumericLabelField";

function NumberField({ settings, value }) {
  const { control } = useForm({ defaultValues: { score: value } });
  return (
    <NumericLabelField
      control={control}
      fieldName="score"
      label="Score"
      settings={settings}
    />
  );
}

NumberField.propTypes = {
  settings: PropTypes.object.isRequired,
  value: PropTypes.number.isRequired,
};

// A browser moves a focused range input one step on an arrow key the way
// stepUp and stepDown do (HTML counts the steps from the input's min), then
// tells the page the value changed. jsdom has no arrow-key default, so the
// press is made of those parts.
const pressArrow = async (slider, key) => {
  await act(async () => {
    slider.focus();
    fireEvent.keyDown(slider, { key });
    if (key === "ArrowRight" || key === "ArrowUp") {
      slider.stepUp();
    } else {
      slider.stepDown();
    }
    fireEvent.input(slider);
    fireEvent.change(slider);
  });
};

const expectValue = async (value) => {
  await waitFor(() =>
    expect(screen.getByRole("slider")).toHaveAttribute(
      "aria-valuenow",
      String(value),
    ),
  );
};

// What the server accepts for a number label with this step, counted the same
// way: from the minimum, or from 0 when the label has none.
const serverAccepts = (value, { min, step }) =>
  Math.abs((value - (min ?? 0)) % step) < 1e-6;

describe("annotate form number field: a label with step_size 2", () => {
  it("steps by 2 from 0 and an arrow press gives 0, 2, 4…", async () => {
    render(
      <NumberField
        settings={{ min: 0, max: 10, step_size: 2, display_type: "slider" }}
        value={4}
      />,
    );
    const slider = screen.getByRole("slider");

    expect(slider).toHaveAttribute("step", "2");
    expect(slider).toHaveAttribute("min", "0");

    await pressArrow(slider, "ArrowRight");
    await expectValue(6);
    expect(serverAccepts(6, { min: 0, step: 2 })).toBe(true);

    await pressArrow(screen.getByRole("slider"), "ArrowLeft");
    await pressArrow(screen.getByRole("slider"), "ArrowLeft");
    await expectValue(2);
  });

  it("counts the steps from the label's minimum, as the server does", async () => {
    render(
      <NumberField
        settings={{ min: 1, max: 9, step_size: 2, display_type: "slider" }}
        value={3}
      />,
    );
    const slider = screen.getByRole("slider");

    expect(slider).toHaveAttribute("step", "2");
    expect(slider).toHaveAttribute("min", "1");

    await pressArrow(slider, "ArrowRight");
    await expectValue(5);
    expect(serverAccepts(5, { min: 1, step: 2 })).toBe(true);

    await pressArrow(screen.getByRole("slider"), "ArrowLeft");
    await pressArrow(screen.getByRole("slider"), "ArrowLeft");
    await expectValue(1);
  });

  it("still steps by stepSize when the label has only that", async () => {
    render(
      <NumberField
        settings={{ min: 0, max: 10, stepSize: 2, displayType: "slider" }}
        value={4}
      />,
    );
    const slider = screen.getByRole("slider");

    expect(slider).toHaveAttribute("step", "2");

    await pressArrow(slider, "ArrowRight");
    await expectValue(6);
  });
});

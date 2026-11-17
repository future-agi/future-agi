// HO S5: an unscored numeric label shows no thumb and no
// filled track and reads `Not scored`; a click on the rail (the minimum
// included) sets the stepped value; a scored label renders as before.
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LabelInput from "src/sections/annotations/queues/annotate/label-input";

const HELP = {
  name: "Helpfulness",
  type: "numeric",
  settings: { min: 0, max: 1, step_size: 0.1, display_type: "slider" },
};
const QUALITY = {
  name: "Quality",
  type: "numeric",
  settings: { min: 1, max: 5, step_size: 1 },
};

// jsdom has no layout: give the slider rail a 100 px box at x = 0.
let rect;
beforeEach(() => {
  rect = vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0,
    top: 0,
    right: 100,
    bottom: 10,
    width: 100,
    height: 10,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
});
afterEach(() => rect.mockRestore());

const rootOf = () => screen.getByRole("slider").closest(".MuiSlider-root");
const press = (x) =>
  fireEvent.mouseDown(rootOf(), { button: 0, clientX: x, clientY: 5 });

describe("unscored slider (HO S5)", () => {
  it("reads Not scored, marks itself unscored and keeps its slider role", () => {
    render(<LabelInput label={HELP} value={null} onChange={vi.fn()} />);
    const slider = screen.getByRole("slider");
    expect(slider).toHaveAttribute("aria-valuetext", "Not scored");
    expect(rootOf()).toHaveAttribute("data-unscored", "true");
    // The thumb sits at the start of the rail, hidden; no filled track.
    expect(rootOf().querySelector(".MuiSlider-thumb").style.left).toBe("0%");
    const css = Array.from(document.querySelectorAll("style"))
      .map((s) => s.textContent)
      .join("\n");
    expect(css).toMatch(
      /\.MuiSlider-thumb:not\(\.Mui-focusVisible\)\{opacity:0;\}/,
    );
    expect(css).toMatch(/\.MuiSlider-track\{display:none;\}/);
  });

  it("a click on the rail sets the stepped value, the minimum included", () => {
    const onChange = vi.fn();
    render(<LabelInput label={HELP} value={null} onChange={onChange} />);
    press(53);
    expect(onChange).toHaveBeenLastCalledWith({ value: 0.5 });
    onChange.mockClear();
    press(0);
    expect(onChange).toHaveBeenLastCalledWith({ value: 0 });
  });

  it("works for a label whose minimum is not 0", () => {
    const onChange = vi.fn();
    render(<LabelInput label={QUALITY} value={null} onChange={onChange} />);
    expect(screen.getByRole("slider")).toHaveAttribute(
      "aria-valuetext",
      "Not scored",
    );
    press(0);
    expect(onChange).toHaveBeenLastCalledWith({ value: 1 });
  });

  it("a scored label renders as before: its value, no Not scored", () => {
    render(
      <LabelInput label={HELP} value={{ value: 0.3 }} onChange={vi.fn()} />,
    );
    const slider = screen.getByRole("slider");
    expect(slider).toHaveAttribute("aria-valuenow", "0.3");
    expect(slider).not.toHaveAttribute("aria-valuetext");
    expect(rootOf()).not.toHaveAttribute("data-unscored");
  });
});

import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import LabelInput from "../../queues/annotate/label-input";
import { numericButtonChoices } from "../numeric-button-choices";

// Both numeric label forms count a button scale's choices with this helper,
// exactly as the annotate screen draws the buttons.
describe("numericButtonChoices", () => {
  it.each([
    [0, 10, 1, 11],
    [0, 20, 1, 21],
    [0, 5, 0.5, 11],
    [0, 1, 0.3, 5],
    [0, 10, 3, 5],
  ])("counts min %s, max %s, step %s as %s choices", (min, max, step, n) => {
    expect(numericButtonChoices(min, max, step)).toHaveLength(n);
  });

  it("adds Max when the steps do not land on it", () => {
    expect(numericButtonChoices(0, 1, 0.3)).toEqual([0, 0.3, 0.6, 0.9, 1]);
    expect(numericButtonChoices(0, 10, 3)).toEqual([0, 3, 6, 9, 10]);
  });

  it("reads values held as text", () => {
    expect(numericButtonChoices("0", "20", "2")).toHaveLength(11);
  });
});

describe("the annotate screen's button scale", () => {
  it.each([
    [0, 10, 1],
    [0, 1, 0.3],
    [0, 10, 3],
    [1, 5, 0.5],
  ])("draws the counted choices for %s, %s, %s", (min, max, step) => {
    render(
      <LabelInput
        label={{
          name: "Score",
          type: "numeric",
          settings: { min, max, step_size: step, display_type: "button" },
        }}
        value={null}
        onChange={vi.fn()}
      />,
    );

    const drawn = screen.getAllByRole("button").map((b) => b.textContent);
    expect(drawn).toEqual(numericButtonChoices(min, max, step).map(String));
  });
});

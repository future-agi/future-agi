import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import DefaultOutput from "../DefaultOutput";

// FA-PROMPTS-01: the Output panel's starting tips only name controls that
// exist on the prompt screen (Run Prompt, Add Message); no EXAMPLES button.
const tips = () => screen.getAllByRole("listitem").map((li) => li.textContent);

describe("DefaultOutput starting tips", () => {
  it("has no broken sentence", () => {
    render(<DefaultOutput />);
    expect(tips().some((t) => /using to/i.test(t))).toBe(false);
  });

  it("never points at an Examples button", () => {
    render(<DefaultOutput />);
    expect(tips().some((t) => /EXAMPLES/.test(t))).toBe(false);
    expect(tips().some((t) => /click examples/i.test(t))).toBe(false);
  });

  it("names the Add Message control for adding turns", () => {
    render(<DefaultOutput />);
    expect(
      tips().some((t) => /Add Message/.test(t) && /conversation/.test(t)),
    ).toBe(true);
  });

  it("names the Run Prompt control", () => {
    render(<DefaultOutput />);
    expect(tips().some((t) => /Run Prompt/.test(t))).toBe(true);
  });
});

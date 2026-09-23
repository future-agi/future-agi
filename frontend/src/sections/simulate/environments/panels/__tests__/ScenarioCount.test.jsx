import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ScenarioCount from "../ScenarioCount";
import { isValidScenarioCount, DEFAULT_SCENARIOS, MAX_SCENARIOS } from "../scenarioCountRules";

describe("isValidScenarioCount", () => {
  it("accepts a whole number in 1..500", () => {
    expect(isValidScenarioCount("10")).toBe(true);
    expect(isValidScenarioCount("1")).toBe(true);
    expect(isValidScenarioCount(String(MAX_SCENARIOS))).toBe(true);
  });

  it("rejects empty, zero, over-cap and non-numeric (empty must block submit)", () => {
    expect(isValidScenarioCount("")).toBe(false);
    expect(isValidScenarioCount("0")).toBe(false);
    expect(isValidScenarioCount("501")).toBe(false);
    expect(isValidScenarioCount("abc")).toBe(false);
    expect(isValidScenarioCount(undefined)).toBe(false);
  });

  it("defaults to 10", () => {
    expect(DEFAULT_SCENARIOS).toBe("10");
    expect(isValidScenarioCount(DEFAULT_SCENARIOS)).toBe(true);
  });
});

describe("ScenarioCount field", () => {
  it("strips non-digits and caps at 500", () => {
    const onChange = vi.fn();
    render(<ScenarioCount value="10" onChange={onChange} />);
    const input = screen.getByLabelText("Number of scenarios to generate");
    fireEvent.change(input, { target: { value: "9x9" } });
    expect(onChange).toHaveBeenLastCalledWith("99");
    fireEvent.change(input, { target: { value: "999" } });
    expect(onChange).toHaveBeenLastCalledWith("500");
  });

  it("marks the input invalid when empty", () => {
    render(<ScenarioCount value="" onChange={() => {}} />);
    expect(screen.getByLabelText("Number of scenarios to generate")).toHaveAttribute("aria-invalid", "true");
  });
});

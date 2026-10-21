import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ScenarioCount from "../ScenarioCount";
import { isValidScenarioCount, DEFAULT_SCENARIOS, MAX_SCENARIOS } from "../scenarioCountRules";

describe("isValidScenarioCount", () => {
  it("accepts a whole number in 1..1000", () => {
    expect(isValidScenarioCount("1")).toBe(true);
    expect(isValidScenarioCount("10")).toBe(true);
    expect(isValidScenarioCount(String(MAX_SCENARIOS))).toBe(true);
  });

  it("rejects empty, zero, over-cap and non-numeric (empty blocks submit)", () => {
    expect(isValidScenarioCount("")).toBe(false);
    expect(isValidScenarioCount("0")).toBe(false);
    expect(isValidScenarioCount("1001")).toBe(false);
    expect(isValidScenarioCount("abc")).toBe(false);
    expect(isValidScenarioCount(undefined)).toBe(false);
  });

  it("caps at 1000 and defaults to 10", () => {
    expect(MAX_SCENARIOS).toBe(1000);
    expect(DEFAULT_SCENARIOS).toBe("10");
  });
});

describe("ScenarioCount field", () => {
  it("strips non-digits and caps input at 1000", () => {
    const onChange = vi.fn();
    render(<ScenarioCount value="10" onChange={onChange} />);
    const input = screen.getByLabelText("Number of scenarios to generate");
    fireEvent.change(input, { target: { value: "9x9" } });
    expect(onChange).toHaveBeenLastCalledWith("99");
    fireEvent.change(input, { target: { value: "1500" } });
    expect(onChange).toHaveBeenLastCalledWith("1000");
  });

  it("shows the designer-style message with the 1000 ceiling", () => {
    render(<ScenarioCount value="10" onChange={() => {}} />);
    expect(screen.getByText(/We'll generate 10 scenarios\. Set any number up to 1000\./)).toBeInTheDocument();
  });

  it("marks the input invalid when empty", () => {
    render(<ScenarioCount value="" onChange={() => {}} />);
    expect(screen.getByLabelText("Number of scenarios to generate")).toHaveAttribute("aria-invalid", "true");
  });
});

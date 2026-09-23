import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ScenarioCount from "../ScenarioCount";
import { DEFAULT_SCENARIOS } from "../scenarioCountRules";

describe("ScenarioCount field", () => {
  it("defaults to 10", () => {
    expect(DEFAULT_SCENARIOS).toBe("10");
  });

  it("strips non-digits and leading zeros (no hard range — the backend gates)", () => {
    const onChange = vi.fn();
    render(<ScenarioCount value="10" onChange={onChange} />);
    const input = screen.getByLabelText("Number of scenarios to generate");
    fireEvent.change(input, { target: { value: "9x9" } });
    expect(onChange).toHaveBeenLastCalledWith("99");
    // No 500 cap anymore; a large number passes through (backend rejects if over).
    fireEvent.change(input, { target: { value: "999" } });
    expect(onChange).toHaveBeenLastCalledWith("999");
  });

  it("shows a generic message and never a range error", () => {
    render(<ScenarioCount value="10" onChange={() => {}} />);
    expect(screen.getByText(/We'll generate 10 scenarios/)).toBeInTheDocument();
    expect(screen.queryByText(/between 1 and/)).toBeNull();
  });

  it("does not mark the input invalid when empty", () => {
    render(<ScenarioCount value="" onChange={() => {}} />);
    expect(screen.getByLabelText("Number of scenarios to generate")).not.toHaveAttribute("aria-invalid", "true");
  });
});

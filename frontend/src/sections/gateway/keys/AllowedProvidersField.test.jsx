import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

import AllowedProvidersField from "./AllowedProvidersField";

vi.mock("./hooks/useKeyProviderOptions", () => ({
  useKeyProviderOptions: () => ({
    options: ["openai", "openrouter"],
    labelFor: (id) =>
      id === "openrouter" ? "Future AGI credits (openrouter)" : id,
  }),
}));

describe("AllowedProvidersField", () => {
  it("offers Future AGI credits as a provider a restricted key may use", () => {
    const onChange = vi.fn();
    render(<AllowedProvidersField value={[]} onChange={onChange} />);

    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(
      screen.getByRole("option", { name: "Future AGI credits (openrouter)" }),
    );

    expect(onChange).toHaveBeenCalledWith(["openrouter"]);
  });

  it("normalizes typed ids, drops empty values and preserves the sentence-case label", () => {
    const onChange = vi.fn();
    render(<AllowedProvidersField value={["openrouter"]} onChange={onChange} gatewayId="gateway-a" />);
    const input = screen.getByRole("combobox", { name: "Allowed providers" });
    fireEvent.change(input, { target: { value: "  custom-provider  " } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    expect(onChange).toHaveBeenLastCalledWith(["openrouter", "custom-provider"]);
    fireEvent.change(input, { target: { value: " openrouter " } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    expect(onChange).toHaveBeenLastCalledWith(["openrouter"]);
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    expect(onChange).toHaveBeenLastCalledWith(["openrouter"]);
  });

  it("shows a chosen Future AGI provider by its label", () => {
    render(<AllowedProvidersField value={["openrouter"]} onChange={vi.fn()} />);
    expect(
      screen.getByText("Future AGI credits (openrouter)"),
    ).toBeInTheDocument();
  });
});

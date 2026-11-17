import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import FilterPanel from "./FilterPanel";

// The Logs Provider column names a managed call's provider "Future AGI
// managed" (by its billing mode), and the Logs API's provider filter takes
// that label for the managed rows (the API's billing_mode). So the Filters
// panel's Provider field offers it to pick, while typed provider names keep
// working as they did.

vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => [],
}));

vi.mock("./hooks/useMetadataValues", () => ({
  default: () => ({ data: { application: [], service: [], tags: [] } }),
}));

const providerField = () => screen.getByText("Provider").parentElement;
const providerInput = () => within(providerField()).getByRole("combobox");

const renderPanel = (filters = {}, props = {}) => {
  const onApply = vi.fn();
  render(
    <FilterPanel
      open
      onClose={vi.fn()}
      filters={filters}
      onApply={onApply}
      {...props}
    />,
  );
  return onApply;
};

describe("FilterPanel Provider: Future AGI managed", () => {
  it("offers Future AGI and applies it as the provider filter", async () => {
    const user = userEvent.setup();
    const onApply = renderPanel();

    await user.click(providerInput());
    await user.click(
      screen.getByRole("option", { name: "Future AGI" }),
    );
    await user.click(screen.getByRole("button", { name: "Apply" }));

    expect(onApply).toHaveBeenCalledWith({ provider: "Future AGI" });
  });

  // A working option kept: green before the change too (a plain text field
  // then), so the input is found by tag, not by its role.
  it("still applies a provider name typed in", async () => {
    const user = userEvent.setup();
    const onApply = renderPanel();

    await user.type(providerField().querySelector("input"), "openai");
    await user.click(screen.getByRole("button", { name: "Apply" }));

    expect(onApply).toHaveBeenCalledWith({ provider: "openai" });
  });

  it("splits a comma-separated list typed in, as the text field took it", async () => {
    const user = userEvent.setup();
    const onApply = renderPanel();

    await user.type(providerInput(), "openai, anthropic{Enter}");
    const field = within(providerField());
    expect(field.getByRole("button", { name: "openai" })).toBeInTheDocument();
    expect(
      field.getByRole("button", { name: "anthropic" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({ provider: "openai,anthropic" });
  });

  it("shows a shared link's label and names as picks, and keeps them", async () => {
    const user = userEvent.setup();
    const onApply = renderPanel({ provider: "Future AGI managed,openai" });

    const field = within(providerField());
    expect(
      field.getByRole("button", { name: "Future AGI managed" }),
    ).toBeInTheDocument();
    expect(field.getByRole("button", { name: "openai" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({
      provider: "Future AGI managed,openai",
    });
  });

  it("says the field takes the label as well as provider names", () => {
    renderPanel();
    expect(providerField()).toHaveTextContent("Future AGI for managed calls, or type provider names");
  });
});

// A deployment that offers no managed models: the field's help names provider
// names only, and Future AGI managed is offered only while the loaded logs
// hold a managed call (one from before managed models were turned off), so
// the option never names a provider no request shown has.
describe("FilterPanel Provider: no managed models", () => {
  const OFF = { managedOffered: false };

  it("names no managed calls in its help", () => {
    renderPanel({}, OFF);
    expect(providerField()).not.toHaveTextContent(/managed/i);
    expect(providerField()).toHaveTextContent("Comma-separated provider names");
  });

  it("offers no Future AGI while no loaded log is a managed call", async () => {
    const user = userEvent.setup();
    renderPanel({}, { ...OFF, managedLogged: false });

    await user.click(providerInput());
    expect(
      screen.queryByRole("option", { name: "Future AGI" }),
    ).toBeNull();
  });

  it("offers Future AGI when the loaded logs hold a managed call, and applies it", async () => {
    const user = userEvent.setup();
    const onApply = renderPanel({}, { ...OFF, managedLogged: true });

    await user.click(providerInput());
    await user.click(
      screen.getByRole("option", { name: "Future AGI" }),
    );
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({ provider: "Future AGI" });
  });

  // A shared link that already filters on the label keeps it as a pick.
  it("keeps a shared link's Future AGI managed pick with none loaded", async () => {
    const user = userEvent.setup();
    const onApply = renderPanel(
      { provider: "Future AGI managed" },
      { ...OFF, managedLogged: false },
    );

    expect(
      within(providerField()).getByRole("button", {
        name: "Future AGI managed",
      }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({ provider: "Future AGI managed" });
  });

  it("still applies a provider name typed in", async () => {
    const user = userEvent.setup();
    const onApply = renderPanel({}, OFF);

    await user.type(providerInput(), "openai, anthropic{Enter}");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({ provider: "openai,anthropic" });
  });
});

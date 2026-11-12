import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import FilterPanel from "./FilterPanel";

vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => [],
}));

vi.mock("./hooks/useMetadataValues", () => ({
  default: () => ({ data: { application: [], service: [], tags: [] } }),
}));

const field = (label) => screen.getByText(label).parentElement;

describe("FilterPanel agent and billing filters", () => {
  it("keeps applied values through a reopen and Apply", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    const filters = {
      billingMode: "byok",
      runId: "run-7",
      agentId: "planner",
      step: "plan,act",
      policyVariant: "stable",
      errorClass: "rate_limited",
    };
    render(
      <FilterPanel
        open
        onClose={vi.fn()}
        filters={filters}
        onApply={onApply}
      />,
    );

    expect(
      within(field("Billing mode")).getByRole("combobox"),
    ).toHaveTextContent("Own key");
    expect(within(field("Step")).getByText("plan")).toBeInTheDocument();
    expect(
      within(field("Error class")).getByText("Rate limited"),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith(filters);
  });

  it("applies new picks as comma-separated filters", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(
      <FilterPanel open onClose={vi.fn()} filters={{}} onApply={onApply} />,
    );

    await user.click(within(field("Billing mode")).getByRole("combobox"));
    await user.click(screen.getByRole("option", { name: "Future AGI credits" }));
    await user.type(within(field("Run ID")).getByRole("textbox"), " run-9 ");
    await user.type(within(field("Agent ID")).getByRole("textbox"), "coder");

    const step = within(field("Step")).getByRole("combobox");
    await user.click(step);
    await user.click(screen.getByRole("option", { name: "verify" }));
    await user.click(screen.getByRole("option", { name: "tool" }));

    const errorClass = within(field("Error class")).getByRole("combobox");
    await user.click(errorClass);
    await user.click(screen.getByRole("option", { name: "Timeout" }));

    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({
      billingMode: "managed",
      runId: "run-9",
      agentId: "coder",
      step: "verify,tool",
      errorClass: "timeout",
    });
  });

  // Routing policies have no page on this console (Policies is hidden), so
  // no request can be told apart by its policy variant: the field is not
  // offered. A variant already in the URL is kept on Apply.
  it("offers no Policy variant field, and keeps one from the URL on Apply", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(
      <FilterPanel
        open
        onClose={vi.fn()}
        filters={{ policyVariant: "stable" }}
        onApply={onApply}
      />,
    );
    expect(screen.queryByText("Policy variant")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({ policyVariant: "stable" });
  });

  // Managed billing is offered only when managed models are available.
  it("offers no Managed billing mode where no managed call can be listed", async () => {
    const user = userEvent.setup();
    render(
      <FilterPanel
        open
        onClose={vi.fn()}
        filters={{}}
        onApply={vi.fn()}
        managedOffered={false}
        managedLogged={false}
      />,
    );
    await user.click(within(field("Billing mode")).getByRole("combobox"));
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Any", "Own key"]);
  });

  it("keeps Managed hidden when unavailable even with historical managed requests", async () => {
    const user = userEvent.setup();
    render(
      <FilterPanel
        open
        onClose={vi.fn()}
        filters={{}}
        onApply={vi.fn()}
        managedOffered={false}
        managedLogged
      />,
    );
    await user.click(within(field("Billing mode")).getByRole("combobox"));
    expect(screen.queryByRole("option", { name: "Future AGI credits" })).toBeNull();
  });

  it("clears them with Clear All", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(
      <FilterPanel
        open
        onClose={vi.fn()}
        filters={{ billingMode: "managed", step: "act", runId: "run-1" }}
        onApply={onApply}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Clear All" }));
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({});
  });
});

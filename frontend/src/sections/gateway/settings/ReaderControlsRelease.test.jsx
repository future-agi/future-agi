import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import RoutingConfigTab from "./RoutingConfigTab";
import GuardrailConfigTab from "./GuardrailConfigTab";

// Disclosure names, not icon rendering, are this spec's contract. The local
// offline runner's missing-icon fallback otherwise prefixes accessible names.
vi.mock("src/components/iconify", () => ({ default: () => null }));

vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: false }),
}));
vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["primary"],
}));

const routing = {
  strategy: "round-robin",
  complexity: { enabled: true, default_tier: "stored-tier" },
  adaptive: { enabled: true },
  provider_lock: { enabled: true, allowed_providers: ["stored-provider"] },
  circuit_breaker: { enabled: true, on_status_codes: [503] },
  retry: { enabled: true, on_status_codes: [429] },
  model_timeouts: { primary: "37s" },
};

describe("Reader disclosures remain readable without draft mutations", () => {
  it.each([
    "Routing Strategy",
    "Complexity Routing",
    "Adaptive Routing",
    "Provider Locking",
    "Circuit Breaker",
    "Retry Configuration",
    "Model Timeouts",
  ])("opens %s with mutations disabled", (title) => {
    const onChange = vi.fn();
    render(<RoutingConfigTab routing={routing} onChange={onChange} />);
    const disclosure = screen.getByRole("button", {
      name: new RegExp(`^${title}`),
    });
    expect(disclosure).toBeEnabled();
    if (disclosure.getAttribute("aria-expanded") !== "true")
      fireEvent.click(disclosure);
    const card = /** @type {HTMLElement} */ (
      disclosure.closest(".MuiCard-root")
    );
    const controls = within(card).queryAllByRole("checkbox");
    for (const control of controls) {
      expect(control).toBeDisabled();
      fireEvent.click(control);
    }
    if (title === "Complexity Routing")
      expect(screen.getByLabelText("Default Tier")).toHaveValue("stored-tier");
    if (title === "Provider Locking")
      expect(screen.getByText("stored-provider")).toBeInTheDocument();
    if (title === "Model Timeouts")
      expect(screen.getByText("37s")).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("shows ordinary stored guardrail settings without Configure, Remove or toggle writes", () => {
    const onChange = vi.fn();
    render(
      <GuardrailConfigTab
        guardrails={{
          checks: {
            "keyword-blocklist": {
              enabled: true,
              action: "block",
              config: { words: ["stored"] },
            },
          },
        }}
        onChange={onChange}
      />,
    );
    const card = /** @type {HTMLElement} */ (
      screen.getByText("Keyword Blocklist").closest(".MuiCard-root")
    );
    for (const control of [
      within(card).getByRole("checkbox"),
      within(card).getByRole("button", { name: "Configure Keyword Blocklist" }),
      within(card).getByRole("button", { name: "Remove" }),
    ]) {
      expect(control).toBeDisabled();
      fireEvent.click(control);
    }
    expect(within(card).getByText("Action: block")).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});

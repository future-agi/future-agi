/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import AlertingConfigTab from "./AlertingConfigTab";
import BudgetsConfigTab from "./BudgetsConfigTab";
import ModelMapConfigTab from "./ModelMapConfigTab";
import ModelDatabaseConfigTab from "./ModelDatabaseConfigTab";
import SecurityConfigTab from "./SecurityConfigTab";
import RoutingConfigTab from "./RoutingConfigTab";
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["primary"],
}));
vi.mock("../providers/hooks/useGatewayConfig", async (original) => ({
  ...(await original()),
  useProviderHealth: () => ({ data: { providers: [] }, isLoading: false }),
}));
vi.mock("@iconify/react", () => ({
  Icon: ({ icon }) => <span data-testid={icon} />,
}));
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid={icon} />,
}));

/** @type {Array<[string, string, (change: (...args: any[]) => void) => React.ReactElement]>} */
const cases = [
  [
    "channel",
    "ops",
    (change) => (
      <AlertingConfigTab
        alerting={{
          enabled: true,
          channels: [{ name: "ops", type: "webhook" }],
        }}
        onChange={change}
      />
    ),
  ],
  [
    "rule",
    "cost-limit",
    (change) => (
      <AlertingConfigTab
        alerting={{
          enabled: true,
          rules: [
            {
              name: "cost-limit",
              metric: "cost_total",
              threshold: 1,
              condition: ">",
            },
          ],
        }}
        onChange={change}
      />
    ),
  ],
  [
    "model mapping",
    "primary",
    (change) => (
      <ModelMapConfigTab
        modelMap={{ primary: "provider-a" }}
        onChange={change}
      />
    ),
  ],
  [
    "model override",
    "primary",
    (change) => (
      <ModelDatabaseConfigTab
        modelDatabase={{ overrides: { primary: {} } }}
        onChange={change}
      />
    ),
  ],
  [
    "redaction pattern",
    "redact-account",
    (change) => (
      <SecurityConfigTab
        privacy={{
          enabled: true,
          redact_patterns: [{ name: "redact-account", pattern: "account" }],
        }}
        onPrivacyChange={change}
        onIpAclChange={vi.fn()}
        onToolPolicyChange={vi.fn()}
      />
    ),
  ],
  [
    "custom pricing",
    "primary",
    (change) => (
      <BudgetsConfigTab
        budgets={{}}
        costTracking={{
          enabled: true,
          custom_pricing: {
            primary: { input_per_mtok: 1, output_per_mtok: 1 },
          },
        }}
        onBudgetsChange={vi.fn()}
        onCostTrackingChange={change}
      />
    ),
  ],
  [
    "model timeout",
    "primary",
    (change) => (
      <RoutingConfigTab
        routing={{ model_timeouts: { primary: "30s" } }}
        onChange={change}
      />
    ),
  ],
];

describe("Org config named removals", () => {
  it.each([
    [
      "Complexity Routing",
      "Complexity tier small",
      {
        complexity: {
          enabled: true,
          tiers: { small: { max_score: 50, model: "primary" } },
        },
      },
      null,
    ],
    [
      "Provider Locking",
      "Provider provider-a from allowed_providers",
      { provider_lock: { enabled: true, allowed_providers: ["provider-a"] } },
      "provider-a",
    ],
    [
      "Circuit Breaker",
      "Circuit breaker status code 503",
      { circuit_breaker: { enabled: true, on_status_codes: [503] } },
      "503",
    ],
    [
      "Retry Configuration",
      "Retry status code 429",
      { retry: { enabled: true, on_status_codes: [429] } },
      "429",
    ],
  ])(
    "confirms and can cancel a removal in %s",
    async (section, label, routing, chip) => {
      const change = vi.fn();
      const user = userEvent.setup();
      render(<RoutingConfigTab routing={routing} onChange={change} />);
      await user.click(screen.getByText(section));
      const remove = async () => {
        if (chip) {
          /** @type {HTMLElement} */ (
            screen.getByText(chip).closest(".MuiChip-root")
          ).focus();
          await user.keyboard("{Delete}");
        } else {
          await user.click(
            screen.getByRole("button", {
              name: "Remove complexity tier small",
            }),
          );
        }
      };
      await remove();
      expect(change).not.toHaveBeenCalled();
      let dialog = screen.getByRole("dialog");
      expect(dialog).toHaveTextContent(label);
      await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
      expect(change).not.toHaveBeenCalled();
      await remove();
      dialog = screen.getByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: "Remove" }));
      expect(change).toHaveBeenCalledTimes(1);
    },
  );
  it.each(cases)(
    "confirms the %s before editing its stored draft",
    (kind, name, make) => {
      const change = vi.fn();
      render(make(change));
      if (kind === "model timeout")
        fireEvent.click(screen.getByText("Model Timeouts"));
      const removalKind =
        {
          channel: "alert channel",
          rule: "alert rule",
          "redaction pattern": "PII pattern",
        }[kind] || kind;
      fireEvent.click(
        screen.getByRole("button", { name: `Remove ${removalKind} ${name}` }),
      );
      expect(change).not.toHaveBeenCalled();
      const dialog = screen.getByRole("dialog");
      expect(dialog).toHaveTextContent(name);
      fireEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
      expect(change).toHaveBeenCalledTimes(1);
    },
  );
});

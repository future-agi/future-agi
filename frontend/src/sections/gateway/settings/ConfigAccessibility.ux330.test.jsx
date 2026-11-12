import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import AlertingConfigTab from "./AlertingConfigTab";
import BudgetsConfigTab from "./BudgetsConfigTab";
import ModelDatabaseConfigTab from "./ModelDatabaseConfigTab";
import ModelMapConfigTab from "./ModelMapConfigTab";
import RoutingConfigTab from "./RoutingConfigTab";
import SecurityConfigTab from "./SecurityConfigTab";

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

const levels = ["Team", "User", "Key", "Tag"];
const budgetProps = {
  budgets: {
    enabled: true,
    teams: { engineering: { limit: 10, per_model: { primary: 5 } } },
    users: { alice: { limit: 10, per_model: { primary: 5 } } },
    keys: { ci: { limit: 10, per_model: { primary: 5 } } },
    tags: { project: { limit: 10, per_model: { primary: 5 } } },
  },
  costTracking: {
    enabled: true,
    custom_pricing: { primary: { input_per_mtok: 1, output_per_mtok: 1 } },
  },
  onBudgetsChange: vi.fn(),
  onCostTrackingChange: vi.fn(),
};

describe("GWUX-03 keyboard-operable budget sections", () => {
  it.each(levels)(
    "opens and closes %s budgets with Enter and Space",
    async (level) => {
      const user = userEvent.setup();
      render(<BudgetsConfigTab {...budgetProps} />);
      const toggle = screen.getByRole("button", { name: `${level} Budgets` });
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      const bodyId = toggle.getAttribute("aria-controls");
      expect(bodyId).toBeTruthy();
      expect(document.getElementById(bodyId)).toBeInTheDocument();
      toggle.focus();
      expect(toggle).toHaveFocus();
      await user.keyboard("{Enter}");
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      await user.keyboard(" ");
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      for (const other of levels.filter((name) => name !== level)) {
        const otherToggle = screen.getByRole("button", {
          name: `${other} Budgets`,
        });
        expect(otherToggle).toHaveAttribute("aria-expanded", "false");
        expect(otherToggle.getAttribute("aria-controls")).not.toBe(bodyId);
      }
      // Clicking the header remains a convenience; clicking its button must not toggle twice.
      await user.click(screen.getByText(`${level} Budgets`));
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      await user.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "false");
    },
  );
});

const cases = [
  [
    "alert channel",
    "Remove alert channel ops",
    () => (
      <AlertingConfigTab
        alerting={{
          enabled: true,
          channels: [{ name: "ops", type: "webhook" }],
        }}
        onChange={vi.fn()}
      />
    ),
  ],
  [
    "alert rule",
    "Remove alert rule cost-limit",
    () => (
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
        onChange={vi.fn()}
      />
    ),
  ],
  [
    "model override",
    "Remove model override primary",
    () => (
      <ModelDatabaseConfigTab
        modelDatabase={{ overrides: { primary: {} } }}
        onChange={vi.fn()}
      />
    ),
  ],
  [
    "model mapping",
    "Remove model mapping primary",
    () => (
      <ModelMapConfigTab
        modelMap={{ primary: "provider-a" }}
        onChange={vi.fn()}
      />
    ),
  ],
  [
    "PII pattern",
    "Remove PII pattern redact-account",
    () => (
      <SecurityConfigTab
        privacy={{
          enabled: true,
          redact_patterns: [{ name: "redact-account", pattern: "account" }],
        }}
        onPrivacyChange={vi.fn()}
        onIpAclChange={vi.fn()}
        onToolPolicyChange={vi.fn()}
      />
    ),
  ],
  [
    "complexity tier",
    "Remove complexity tier small",
    () => (
      <RoutingConfigTab
        routing={{
          complexity: {
            enabled: true,
            tiers: { small: { max_score: 50, model: "primary" } },
          },
        }}
        onChange={vi.fn()}
      />
    ),
    "Complexity Routing",
  ],
  [
    "model timeout",
    "Remove model timeout primary",
    () => (
      <RoutingConfigTab
        routing={{ model_timeouts: { primary: "30s" } }}
        onChange={vi.fn()}
      />
    ),
    "Model Timeouts",
  ],
];

describe("GWUX-04 named configuration icon buttons", () => {
  it.each(cases)(
    "names the %s removal action and its object",
    async (_kind, name, element, section) => {
      render(element());
      if (section) await userEvent.click(screen.getByText(section));
      expect(screen.getByRole("button", { name })).toHaveAccessibleName(name);
    },
  );

  it.each([
    ["Team", "engineering"],
    ["User", "alice"],
    ["Key", "ci"],
    ["Tag", "project"],
  ])(
    "names %s budget entry and per-model limit removals",
    async (level, entry) => {
      render(<BudgetsConfigTab {...budgetProps} />);
      // Header click allows this naming regression to fail independently of GWUX-03.
      await userEvent.click(screen.getByText(`${level} Budgets`));
      expect(
        screen.getByRole("button", {
          name: `Remove ${level.toLowerCase()} budget ${entry}`,
        }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Remove model limit primary" }),
      ).toBeInTheDocument();
    },
  );

  it("names the per-model limit removal independently of its parent entry", async () => {
    render(<BudgetsConfigTab {...budgetProps} />);
    await userEvent.click(screen.getByText("Team Budgets"));
    expect(
      screen.getByRole("button", { name: "Remove model limit primary" }),
    ).toBeInTheDocument();
  });

  it("names the custom pricing removal action", () => {
    render(<BudgetsConfigTab {...budgetProps} />);
    expect(
      screen.getByRole("button", { name: "Remove custom pricing primary" }),
    ).toBeInTheDocument();
  });
});

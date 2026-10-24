// An alert rule shows Active only when this gateway
// reports that it enforces org alerting (the gateway's
// org_sections_enforced, passed through by the config action); otherwise it
// says what is true. Severity and cooldown show what the gateway applies.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import AlertingMonitoringSection from "./AlertingMonitoringSection";

const mock = vi.hoisted(() => ({ config: null }));

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ tab: "rules" }),
  useNavigate: () => vi.fn(),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default", isLoading: false }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: mock.config, isLoading: false }),
  useProviderHealth: () => ({ data: null }),
  useUpdateConfig: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
}));
vi.mock("../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: null }),
}));
vi.mock("./CreateAlertRuleDialog", () => ({ default: () => null }));
vi.mock("./CreateChannelDialog", () => ({ default: () => null }));

const RULE = {
  name: "high-errors",
  metric: "error_count",
  condition: ">=",
  threshold: 10,
  window: "5m",
  cooldown: "30m",
  severity: "critical",
  channels: ["ops"],
};

function setConfig({ enforced, alertingEnabled = true, rules = [RULE] } = {}) {
  mock.config = {
    alerting: { enabled: alertingEnabled, rules, channels: [] },
    gateway: { status: "healthy" },
  };
  if (enforced !== undefined) mock.config.org_sections_enforced = enforced;
}

function ruleRow(name) {
  return screen.getByText(name).closest("tr");
}

describe("Alert Rules status chip", () => {
  beforeEach(() => {
    mock.config = null;
  });

  it("is Active when the gateway enforces alerting", () => {
    setConfig({ enforced: ["alerting", "budgets"] });
    render(<AlertingMonitoringSection />);
    expect(
      within(ruleRow("high-errors")).getByText("Active"),
    ).toBeInTheDocument();
  });

  it("says 'Not enforced on this gateway' when the gateway does not list alerting", () => {
    setConfig({ enforced: ["budgets", "cache"] });
    render(<AlertingMonitoringSection />);
    const row = ruleRow("high-errors");
    expect(
      within(row).getByText("Not enforced on this gateway"),
    ).toBeInTheDocument();
    expect(within(row).queryByText("Active")).not.toBeInTheDocument();
  });

  it("says 'Not enforced on this gateway' for an empty list", () => {
    setConfig({ enforced: [] });
    render(<AlertingMonitoringSection />);
    expect(
      within(ruleRow("high-errors")).getByText("Not enforced on this gateway"),
    ).toBeInTheDocument();
  });

  it("says 'Enforcement unknown' when the gateway does not report the field", () => {
    setConfig({});
    render(<AlertingMonitoringSection />);
    const row = ruleRow("high-errors");
    expect(within(row).getByText("Enforcement unknown")).toBeInTheDocument();
    expect(within(row).queryByText("Active")).not.toBeInTheDocument();
  });

  it("is never Active while the org's alerting switch is off", () => {
    setConfig({ enforced: ["alerting"], alertingEnabled: false });
    render(<AlertingMonitoringSection />);
    const row = ruleRow("high-errors");
    expect(within(row).getByText("Alerting off")).toBeInTheDocument();
    expect(within(row).queryByText("Active")).not.toBeInTheDocument();
  });

  it("shows a disabled rule as Disabled", () => {
    setConfig({
      enforced: ["alerting"],
      rules: [{ ...RULE, enabled: false }],
    });
    render(<AlertingMonitoringSection />);
    expect(
      within(ruleRow("high-errors")).getByText("Disabled"),
    ).toBeInTheDocument();
  });
});

describe("Alert Rules severity and cooldown", () => {
  it("shows the stored severity and cooldown", () => {
    setConfig({ enforced: ["alerting"] });
    render(<AlertingMonitoringSection />);
    const row = ruleRow("high-errors");
    expect(within(row).getByText("critical")).toBeInTheDocument();
    expect(within(row).getByText("30m")).toBeInTheDocument();
  });

  it("a rule saved without either shows what the gateway applies: warning, 15m", () => {
    const { severity: _s, cooldown: _c, ...bare } = RULE;
    setConfig({ enforced: ["alerting"], rules: [bare] });
    render(<AlertingMonitoringSection />);
    const row = ruleRow("high-errors");
    expect(within(row).getByText("warning")).toBeInTheDocument();
    expect(within(row).queryByText("info")).not.toBeInTheDocument();
    expect(within(row).getByText("15m (default)")).toBeInTheDocument();
  });
});

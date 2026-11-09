import React, { forwardRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import { ENFORCEMENT } from "../enforcement";
import GuardrailConfigTab from "../GuardrailConfigTab";
import RoutingConfigTab from "../RoutingConfigTab";
import CacheConfigTab from "../CacheConfigTab";
import RateLimitingConfigTab from "../RateLimitingConfigTab";
import BudgetsConfigTab from "../BudgetsConfigTab";
import SecurityConfigTab from "../SecurityConfigTab";
import AlertingConfigTab from "../AlertingConfigTab";
import MCPConfigTab from "../MCPConfigTab";
import ModelDatabaseConfigTab from "../ModelDatabaseConfigTab";
import ModelMapConfigTab from "../ModelMapConfigTab";

// An option this gateway stores but never applies is never editable. Every
// "Not enforced on this gateway" note on an Org config editor tab sits in a
// read-only block (data-read-only) that holds no enabled input, switch,
// checkbox, picker or option; a stored value shows there beside the chip.
// This walks every tab with a config that stores every such setting, and
// every id in enforcement.js, so a new id or a new control fails here first.

vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid() {
    return <div data-testid="ag-grid" />;
  }),
}));
vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));
vi.mock("../../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["gpt-4o", "gpt-4o-mini", "claude-sonnet-4"],
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default" }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: {}, isLoading: false }),
  useProviderHealth: () => ({ data: { providers: [] } }),
}));

// Every not-enforced setting stored, next to the ones the gateway applies.
const FULL = {
  guardrails: { timeout_ms: 4000, fail_open: true, pipeline_mode: "parallel" },
  routing: {
    strategy: "weighted",
    default_model: "gpt-4o",
    fallback_enabled: true,
    model_fallbacks: { "gpt-4o": ["gpt-4o-mini"] },
    conditional_routes: [
      { field: "metadata.tier", operator: "$eq", value: "gold", target: "x" },
    ],
    fastest: { enabled: true, max_concurrent: 3, cancel_delay: "100ms" },
    adaptive: { enabled: true, learning_requests: 100 },
    retry: { enabled: true, max_retries: 2 },
    circuit_breaker: { enabled: true, failure_threshold: 5 },
    failover: { enabled: true, max_attempts: 3 },
    scheduled: { enabled: true, max_pending_jobs: 10 },
    access_groups: { beta: { models: ["gpt-4o"], description: "Beta" } },
    mirror: {
      enabled: true,
      rules: [{ source_model: "gpt-4o", target_provider: "anthropic" }],
    },
    complexity: { enabled: true },
    provider_lock: { enabled: true, allowed_providers: ["openai"] },
    model_timeouts: { "gpt-4o": "30s" },
  },
  cache: {
    enabled: true,
    default_ttl: "10m",
    max_entries: 20000,
    backend: "redis",
    redis: { address: "cache.internal:6379", password: "not-for-screens" },
  },
  rate_limiting: {
    enabled: true,
    global_rpm: 600,
    global_tpm: 90000,
    per_key_rpm: 60,
    per_key_tpm: 9000,
    per_model_rpm: 300,
    per_user_rpm: 30,
  },
  budgets: {
    enabled: true,
    org_limit: { limit: 500, on_exceed: "warn" },
    hard_limit: { limit: 300, on_exceed: "block" },
  },
  alerting: {
    enabled: true,
    rules: [
      {
        name: "errors",
        metric: "error_count",
        condition: ">=",
        threshold: 5,
        window: "5m",
        cooldown: "15m",
        severity: "critical",
      },
    ],
  },
  mcp: {
    enabled: true,
    servers: {},
    guardrails: {
      enabled: true,
      blocked_tools: ["shell"],
      allowed_servers: ["github"],
      tool_rate_limits: { search: 10 },
    },
  },
  model_database: {
    overrides: { "gpt-4o": { max_input_tokens: 128000 } },
  },
  model_map: { "my-model": "openai" },
};

const noop = () => {};

// The Org config editor's tabs and the not-enforced ids each shows
// (read-only) for FULL.
const TABS = [
  {
    name: "Guardrails",
    render: () => (
      <GuardrailConfigTab guardrails={FULL.guardrails} onChange={noop} />
    ),
    ids: [],
  },
  {
    name: "Routing",
    render: () => <RoutingConfigTab routing={FULL.routing} onChange={noop} />,
    // Not shown: the tab says in one line that they are stored and kept.
    ids: [],
  },
  {
    name: "Cache",
    render: () => <CacheConfigTab cache={FULL.cache} onChange={noop} />,
    ids: [],
  },
  {
    name: "Rate Limiting",
    render: () => (
      <RateLimitingConfigTab
        rateLimiting={FULL.rate_limiting}
        onChange={noop}
      />
    ),
    ids: [],
  },
  {
    name: "Budgets",
    render: () => (
      <BudgetsConfigTab
        budgets={FULL.budgets}
        costTracking={{}}
        onBudgetsChange={noop}
        onCostTrackingChange={noop}
      />
    ),
    ids: [],
  },
  {
    name: "Security",
    render: () => (
      <SecurityConfigTab
        ipAcl={{}}
        privacy={{}}
        toolPolicy={{}}
        onIpAclChange={noop}
        onPrivacyChange={noop}
        onToolPolicyChange={noop}
      />
    ),
    ids: [],
  },
  {
    name: "Alerting",
    render: () => (
      <AlertingConfigTab alerting={FULL.alerting} onChange={noop} />
    ),
    ids: [],
  },
  {
    name: "MCP",
    render: () => <MCPConfigTab mcp={FULL.mcp} onChange={noop} />,
    ids: ["mcp.guardrails"],
  },
  {
    name: "Model Database",
    render: () => (
      <ModelDatabaseConfigTab
        modelDatabase={FULL.model_database}
        onChange={noop}
      />
    ),
    ids: [],
  },
  {
    name: "Model Map",
    render: () => (
      <ModelMapConfigTab modelMap={FULL.model_map} onChange={noop} />
    ),
    ids: [],
  },
];

// Ids no Org config editor tab shows, and the page outside this folder that
// does (its own tests hold it).
const ELSEWHERE = {
  "guardrails.failOpen": "guardrails/rules/PipelineSettings.jsx",
  "guardrails.pipelineMode": "guardrails/rules/PipelineSettings.jsx",
  "cache.backends": "providers/CacheStatusView.jsx",
  "customProperties.rules": "custom-properties/CreateEditPropertyDialog.jsx",
  // Not shown on the Routing tab, which says in one line that these stored
  // settings are kept; the Org config Sections table names each one.
  "routing.defaultModel": "settings/orgConfigSections.js",
  "routing.conditionalRoutes": "settings/orgConfigSections.js",
  "routing.fastest": "settings/orgConfigSections.js",
  "routing.unsupportedKeys": "settings/orgConfigSections.js",
};

const VALUE_CONTROLS = [
  "input:not([type=hidden]):not([aria-hidden=true])",
  "textarea:not([aria-hidden=true])",
  "select",
  "[role=combobox]",
  "[role=switch]",
  "[role=checkbox]",
  "[role=radio]",
  "[role=slider]",
  "[role=spinbutton]",
  "[role=textbox]",
  "[role=option]",
  "[contenteditable=true]",
].join(", ");

function enabledControls(element) {
  return [...element.querySelectorAll(VALUE_CONTROLS)].filter(
    (control) =>
      !control.disabled && control.getAttribute("aria-disabled") !== "true",
  );
}

describe("not-enforced settings are read-only on every Org config tab", () => {
  it.each(TABS.map((tab) => [tab.name, tab]))(
    "%s: every not-enforced note sits in a read-only block with no enabled control",
    (_name, tab) => {
      const { container, unmount } = render(tab.render());
      const notes = [
        ...container.querySelectorAll("[data-enforcement-kind=not-enforced]"),
      ];
      const shown = notes.map((note) =>
        note.getAttribute("data-testid").replace(/^enforcement-/, ""),
      );
      expect([...new Set(shown)].sort()).toEqual([...tab.ids].sort());
      for (const note of notes) {
        const block = note.closest("[data-read-only]");
        expect(block, note.getAttribute("data-testid")).not.toBeNull();
        expect(enabledControls(block)).toEqual([]);
      }
      unmount();
    },
  );

  it("shows every enforcement id somewhere", () => {
    const walked = new Set(TABS.flatMap((tab) => tab.ids));
    for (const id of Object.keys(ENFORCEMENT)) {
      expect(walked.has(id) || id in ELSEWHERE, id).toBe(true);
    }
  });

  it("paints no stored secret on any tab", () => {
    for (const tab of TABS) {
      const { unmount } = render(tab.render());
      expect(screen.queryByText(/not-for-screens/)).toBeNull();
      unmount();
    }
  });
});

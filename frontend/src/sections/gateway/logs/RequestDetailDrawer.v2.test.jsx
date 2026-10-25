import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import RequestDetailDrawer from "./RequestDetailDrawer";

const { detail, mockEnqueueSnackbar } = vi.hoisted(() => ({
  detail: { current: null },
  mockEnqueueSnackbar: vi.fn(),
}));

vi.mock("./hooks/useRequestDetail", () => ({
  default: () => ({
    data: { result: detail.current },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("../guardrails/FeedbackWidget", () => ({ default: () => null }));

vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));

// A log from before log entry v2, as the detail endpoint returns it: the v2
// blocks are present but hold nothing, and there are no attempts.
const OLD_LOG = {
  id: "log-old",
  request_id: "req-old-1234567890",
  model: "gpt-4o-mini",
  provider: "openai",
  status_code: 200,
  latency_ms: 300,
  input_tokens: 10,
  output_tokens: 20,
  cost: "0.000500",
  started_at: "2026-05-21T10:02:00Z",
  is_stream: false,
  cache_hit: false,
  fallback_used: false,
  guardrail_triggered: false,
  is_error: false,
  routing_strategy: "cost",
  resolved_model: "gpt-4o-mini",
  api_key_id: "key-1",
  user_id: "user-1",
  session_id: "session-1",
  metadata: {},
  generation_id: null,
  attempt_count: 1,
  attempts: [],
  billing: {
    mode: null,
    credential_type: null,
    cost_upstream: null,
    cost_markup: null,
    billed: "0.000500",
    cost_source: null,
    usage_estimated: null,
  },
  agent: {
    framework: null,
    agent_id: null,
    agent_version: null,
    run_id: null,
    parent_run_id: null,
    root_run_id: null,
    task_id: null,
    workflow: null,
    step: null,
    step_source: null,
    turn: null,
    env: null,
    budget_scope: null,
    identity_source: null,
  },
  routing: {
    policy_version: null,
    policy_variant: null,
    catalog_snapshot_id: null,
    auto_routed: false,
  },
  guardrail: null,
};

const ATTEMPT = {
  index: 1,
  attempt_id: "req-v2.1",
  kind: "primary",
  reason: "",
  provider: "openrouter",
  model: "anthropic/claude-sonnet-5.5",
  endpoint_id: "anthropic",
  credential_type: "managed",
  status_code: 200,
  outcome: "success",
  latency_ms: 840,
  ttfb_ms: 200,
  prompt_tokens: 100,
  completion_tokens: 50,
  upstream_cost_micros: 1200,
  billed_cost_micros: 1260,
  billable: true,
};

const AUTO_DECISION = {
  model: "anthropic/claude-sonnet-5.5",
  provider: "openrouter",
  task_tier: "standard",
  complexity: 12,
  preference: "balanced",
  considered: 20,
  ranked: [],
  affinity: "broken:switch_paid",
  constraints: {
    call_cap_usd: 0.05,
    deadline_ms: 3000,
    require: ["tools", "json"],
    route_hint: "cost",
    data_policy: "zdr",
    request_provider: "openrouter",
  },
};

const V2_LOG = {
  ...OLD_LOG,
  id: "log-v2",
  request_id: "req-v2",
  generation_id: "req-v2",
  model: "auto",
  attempt_count: 2,
  attempts: [
    ATTEMPT,
    {
      ...ATTEMPT,
      index: 2,
      attempt_id: "req-v2.2",
      kind: "hedge",
      outcome: "discarded",
      billable: false,
    },
  ],
  metadata: {
    billing_mode: "managed",
    cost_base: "0.0012",
    cost_markup_percent: "5",
    cost_source: "provider_reported",
    auto_model: "anthropic/claude-sonnet-5.5",
    auto_task_tier: "standard",
    auto_decision: JSON.stringify(AUTO_DECISION),
  },
  billing: {
    ...OLD_LOG.billing,
    mode: "managed",
    credential_type: "managed",
    cost_upstream: "0.00120000",
    cost_markup: "0.00006000",
    cost_source: "provider_reported",
    usage_estimated: true,
  },
  agent: {
    ...OLD_LOG.agent,
    framework: "omega",
    agent_id: "planner",
    agent_version: "3",
    run_id: "run/7",
    parent_run_id: "run-6",
    root_run_id: "root-1",
    task_id: "task-9",
    workflow: "research",
    step: "act",
    step_source: "header",
    turn: 4,
    env: "staging",
    budget_scope: "ledger-1",
  },
  routing: {
    policy_version: "v12",
    policy_variant: "candidate",
    catalog_snapshot_id: "snap-42",
    auto_routed: true,
  },
};

const openDrawer = () =>
  render(<RequestDetailDrawer open logId="x" onClose={vi.fn()} />);

const section = (heading) =>
  screen.getByRole("heading", { name: heading }).parentElement;

describe("RequestDetailDrawer, request explorer v2", () => {
  beforeEach(() => {
    mockEnqueueSnackbar.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders a log without v2 fields with no attempts, agent, policy or copy button", () => {
    detail.current = OLD_LOG;
    openDrawer();
    expect(screen.getByText("Strategy:")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /^Attempts/ })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Agent" })).toBeNull();
    expect(screen.queryByText("Policy:")).toBeNull();
    expect(screen.queryByText("Catalog snapshot:")).toBeNull();
    expect(screen.queryByText("Billing")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Copy generation id" }),
    ).toBeNull();
    expect(screen.getByText("Fallback Used: No")).toBeInTheDocument();
  });

  it("shows the attempts timeline on the Overview tab", () => {
    detail.current = V2_LOG;
    openDrawer();
    const list = screen.getByRole("list", { name: "Attempts" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[1]).getByText("Hedge")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Discarded")).toBeInTheDocument();
  });

  // The run ids are plain text, not links.
  it("shows the agent context with its run ids as text", () => {
    detail.current = V2_LOG;
    openDrawer();
    const agent = section("Agent");
    expect(agent).toHaveTextContent("planner (version 3) · omega");
    expect(agent).toHaveTextContent("Run: run/7");
    expect(agent).toHaveTextContent("Parent run: run-6");
    expect(agent).toHaveTextContent("Root run: root-1");
    expect(within(agent).queryByRole("link")).toBeNull();
    expect(agent).toHaveTextContent("Task: task-9");
    expect(agent).toHaveTextContent("Workflow: research");
    expect(agent).toHaveTextContent("Turn: 4");
    expect(agent).toHaveTextContent("Environment: staging");
    expect(agent).toHaveTextContent("Budget scope: ledger-1");
  });

  it("shows step, affinity, constraints, policy and catalog snapshot in the auto block", () => {
    detail.current = V2_LOG;
    openDrawer();
    const auto = section("Auto routing");
    expect(within(auto).getByText("Step:").parentElement).toHaveTextContent(
      "act (header)",
    );
    expect(within(auto).getByText("Affinity:").parentElement).toHaveTextContent(
      "Broken: switch_paid",
    );
    const constraints = within(auto).getByText("Constraints:").parentElement;
    expect(constraints).toHaveTextContent("Call cap $0.05");
    expect(constraints).toHaveTextContent("Deadline 3.00s");
    expect(constraints).toHaveTextContent("Requires tools, json");
    expect(constraints).toHaveTextContent("Route hint cost");
    expect(constraints).toHaveTextContent("Data policy zdr");
    expect(constraints).toHaveTextContent("Provider openrouter");
    expect(within(auto).getByText("Policy:").parentElement).toHaveTextContent(
      "v12 (candidate)",
    );
    expect(
      within(auto).getByText("Catalog snapshot:").parentElement,
    ).toHaveTextContent("snap-42");
    // Shown once, in the auto block, not again under Routing.
    expect(screen.getAllByText("Policy:")).toHaveLength(1);
  });

  it("leaves out the affinity and constraints a decision does not carry", () => {
    const today = { ...AUTO_DECISION };
    delete today.affinity;
    delete today.constraints;
    detail.current = {
      ...V2_LOG,
      metadata: { ...V2_LOG.metadata, auto_decision: JSON.stringify(today) },
      agent: { ...V2_LOG.agent, step: null, step_source: null },
    };
    openDrawer();
    const auto = section("Auto routing");
    expect(within(auto).queryByText("Affinity:")).toBeNull();
    expect(within(auto).queryByText("Constraints:")).toBeNull();
    expect(within(auto).queryByText("Step:")).toBeNull();
    expect(within(auto).getByText("Policy:")).toBeInTheDocument();
  });

  it("shows the policy under Routing for a request that did not use auto", () => {
    detail.current = {
      ...V2_LOG,
      metadata: { billing_mode: "managed" },
      routing: { ...V2_LOG.routing, auto_routed: false },
    };
    openDrawer();
    expect(screen.queryByRole("heading", { name: "Auto routing" })).toBeNull();
    const routing = section("Routing");
    expect(
      within(routing).getByText("Policy:").parentElement,
    ).toHaveTextContent("v12 (candidate)");
    expect(
      within(routing).getByText("Catalog snapshot:").parentElement,
    ).toHaveTextContent("snap-42");
  });

  it("shows the credential type and estimated usage in the billing block", () => {
    detail.current = V2_LOG;
    openDrawer();
    const billing = section("Billing");
    expect(
      within(billing).getByText("Credential:").parentElement,
    ).toHaveTextContent("Managed");
    expect(billing).toHaveTextContent(
      "Usage was estimated: the provider reported no token counts.",
    );
  });

  it("copies the generation id", async () => {
    const user = userEvent.setup();
    // user-event installs a clipboard stub on setup.
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    detail.current = V2_LOG;
    openDrawer();
    await user.click(
      screen.getByRole("button", { name: "Copy generation id" }),
    );
    expect(writeText).toHaveBeenCalledWith("req-v2");
    await waitFor(() =>
      expect(mockEnqueueSnackbar).toHaveBeenCalledWith("Generation id copied", {
        variant: "success",
      }),
    );
  });
});

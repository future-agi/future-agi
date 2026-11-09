import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import RequestDetailDrawer from "./RequestDetailDrawer";

const { state } = vi.hoisted(() => ({
  state: { data: null, isLoading: false, error: null, refetch: () => {} },
}));

vi.mock("./hooks/useRequestDetail", () => ({ default: () => state }));
vi.mock("../guardrails/FeedbackWidget", () => ({
  default: () => <div>Feedback widget</div>,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
// Run links as they are with the runs pages routed.
vi.mock("../shell/page-status", async (importOriginal) => ({
  ...(await importOriginal()),
  isGatewayPageHidden: () => false,
}));

const FALLBACK_LOG = {
  id: "0daa66e9",
  request_id: "01M5CASWE8X9X18KSVMAQ7Z3PE",
  model: "gpt-4-turbo",
  resolved_model: "gpt-4o-mini",
  provider: "openai",
  status_code: 200,
  latency_ms: 470,
  started_at: "2026-10-20T09:30:45.155605Z",
  input_tokens: 7,
  output_tokens: 7,
  cost: "0.000005",
  fallback_used: true,
  cache_hit: false,
  is_stream: false,
  guardrail_triggered: false,
  is_error: false,
  api_key_id: "key_a1a3",
  session_id: "sess-0015",
  metadata: { auth_key_name: "checkout-service" },
  request_headers: {},
  response_headers: {},
  attempts: [
    {
      index: 1,
      kind: "primary",
      provider: "flaky-upstream",
      model: "gpt-4-turbo",
      status_code: 502,
      outcome: "error",
      error_class: "upstream_5xx",
      started_at: "2026-10-20T09:30:45.157177Z",
      latency_ms: 214,
      billable: false,
    },
    {
      index: 2,
      kind: "model_fallback",
      reason: "status_5xx",
      provider: "openai",
      model: "gpt-4o-mini",
      status_code: 200,
      outcome: "success",
      started_at: "2026-10-20T09:30:45.371346Z",
      latency_ms: 99,
      billable: true,
      billed_cost_micros: 5,
      upstream_cost_micros: 5,
    },
  ],
  billing: {
    mode: "byok",
    credential_type: null,
    cost_upstream: "0.00000500",
    cost_markup: null,
    billed: "0.000005",
    cost_source: "modeldb",
    usage_estimated: false,
  },
  agent: {},
  routing: {},
};

const open = (log = FALLBACK_LOG) => {
  state.data = { result: log };
  state.error = null;
  state.isLoading = false;
  return render(<RequestDetailDrawer open logId={log.id} onClose={vi.fn()} />);
};

afterEach(() => vi.restoreAllMocks());

describe("Request detail drawer, timeline", () => {
  it("heads with the full request id, a copy button and the outcome", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    open();
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByRole("heading", {
        name: "01M5CASWE8X9X18KSVMAQ7Z3PE",
      }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Copy request id" }));
    expect(writeText).toHaveBeenCalledWith("01M5CASWE8X9X18KSVMAQ7Z3PE");
    const summary = within(screen.getByRole("list", { name: "Summary" }));
    expect(summary.getByText("470 ms")).toBeInTheDocument();
    expect(summary.getByText("7 in / 7 out")).toBeInTheDocument();
    expect(summary.getByText("$0.000005")).toBeInTheDocument();
    expect(summary.getByText("Own key")).toBeInTheDocument();
    expect(screen.getByText("gpt-4-turbo → gpt-4o-mini")).toBeInTheDocument();
  });

  it("draws attempts and the fallback on one time axis", () => {
    open();
    const timeline = within(
      screen.getByRole("list", { name: "Request timeline" }),
    );
    const rows = timeline.getAllByRole("listitem");
    expect(rows.map((r) => r.getAttribute("data-kind"))).toEqual([
      "received",
      "attempt",
      "attempt",
      "response",
    ]);
    expect(rows[1]).toHaveTextContent("Primary · flaky-upstream / gpt-4-turbo");
    expect(rows[1]).toHaveTextContent("502 · Upstream 5xx");
    expect(rows[1]).toHaveTextContent("+2 ms");
    expect(rows[1]).toHaveTextContent("214 ms");
    expect(rows[2]).toHaveTextContent("Model fallback · openai / gpt-4o-mini");
    expect(rows[2]).toHaveTextContent("Billed");
    // Bars are placed and sized on the request's axis (470 ms).
    const bar = rows[1].querySelector("[data-bar]");
    expect(bar.style.left).toBe("0.43%");
    expect(bar.style.width).toBe("45.53%");
  });

  it("shows only the flags that are true", () => {
    open();
    expect(screen.getByText("Fallback used")).toBeInTheDocument();
    expect(screen.queryByText(/Streaming/)).toBeNull();
    expect(screen.queryByText(/: No$/)).toBeNull();
  });

  it("links the session, key and run to the logs and run pages", () => {
    open({
      ...FALLBACK_LOG,
      agent: { run_id: "run-support-009", agent_id: "support-agent" },
    });
    expect(
      screen.getByRole("link", { name: "All requests in this session" }),
    ).toHaveAttribute("href", "/dashboard/gateway/logs?session_id=sess-0015");
    expect(
      screen.getByRole("link", { name: "All requests with this key" }),
    ).toHaveAttribute("href", "/dashboard/gateway/logs?api_key_id=key_a1a3");
    expect(
      screen.getByRole("link", { name: "run-support-009" }),
    ).toHaveAttribute("href", "/dashboard/gateway/runs/run-support-009");
  });

  it("says plainly when no headers were recorded", async () => {
    const user = userEvent.setup();
    open();
    await user.click(screen.getByRole("tab", { name: "Headers" }));
    expect(
      screen.getAllByText("The gateway recorded no headers for this request."),
    ).toHaveLength(2);
  });

  it("shows request headers in a table when there are some", async () => {
    const user = userEvent.setup();
    open({
      ...FALLBACK_LOG,
      request_headers: { "x-agentcc-session-id": "sess-0015" },
    });
    await user.click(screen.getByRole("tab", { name: "Headers" }));
    expect(screen.getByText("x-agentcc-session-id")).toBeInTheDocument();
  });

  it("shows a guardrail block in the timeline with no upstream call", () => {
    open({
      ...FALLBACK_LOG,
      status_code: 403,
      latency_ms: 4,
      guardrail_triggered: true,
      fallback_used: false,
      guardrail_results: [
        {
          name: "keyword-blocklist",
          action: "block",
          message: "Blocked keywords found: internal only",
        },
      ],
      attempts: [],
    });
    const timeline = within(
      screen.getByRole("list", { name: "Request timeline" }),
    );
    expect(
      timeline.getByText("Guardrail keyword-blocklist: blocked"),
    ).toBeInTheDocument();
    expect(timeline.getByText("No upstream call was made")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Guardrails" })).toBeInTheDocument();
  });

  it("shows a calm error with Retry", async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    state.data = null;
    state.error = { message: "Request log not found" };
    state.refetch = refetch;
    render(<RequestDetailDrawer open logId="x" onClose={vi.fn()} />);
    expect(
      screen.getByText("This request could not be loaded"),
    ).toBeInTheDocument();
    expect(screen.getByText("Request log not found")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
    state.error = null;
  });
});

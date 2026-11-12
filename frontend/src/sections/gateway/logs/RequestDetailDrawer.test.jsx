import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  fireEvent,
  within,
} from "src/utils/test-utils";
import RequestDetailDrawer from "./RequestDetailDrawer";
import { CREDITS, creditsLog, ownKeyLog } from "./__tests__/priceFixtures";

// Fields a test adds to the log the mocked detail hook returns.
const { extraDetail } = vi.hoisted(() => ({ extraDetail: { current: {} } }));

vi.mock("./hooks/useRequestDetail", () => ({
  default: () => ({
    data: {
      result: {
        ...extraDetail.current,
        id: "log-detail-id",
        request_id: "req-detail-1234567890",
        model: "gpt-4o-mini",
        provider: "openai",
        status_code: 200,
        latency_ms: 789,
        input_tokens: 30,
        output_tokens: 40,
        cost: "0.002000",
        started_at: "2026-05-21T10:02:00Z",
        is_stream: true,
        cache_hit: true,
        fallback_used: true,
        guardrail_triggered: true,
        is_error: false,
        routing_strategy: "cost",
        resolved_model: "gpt-4o-mini-2026",
        api_key_id: "key-1",
        user_id: "user-1",
        session_id: "session-1",
        request_body: { messages: [{ role: "user", content: "hello" }] },
        response_body: { choices: [{ message: { content: "hi" } }] },
        request_headers: { "x-test": "request" },
        response_headers: { "x-test": "response" },
        guardrail_results: {
          action: "warn",
          checks: [{ name: "pii", action: "warn", latency_ms: 12 }],
        },
      },
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("../guardrails/FeedbackWidget", () => ({
  default: () => <div>Feedback widget</div>,
}));

describe("RequestDetailDrawer", () => {
  it("keeps request traversal explicit and never captures typing or arrow keys in body inputs", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(
      <RequestDetailDrawer
        open
        logId="log-detail-id"
        onClose={vi.fn()}
        navigation={{
          ready: true,
          position: 2,
          count: 3,
          previousId: "previous",
          nextId: "next",
        }}
        onNavigate={onNavigate}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Next request" }));
    expect(onNavigate).toHaveBeenLastCalledWith("next");
    await user.click(screen.getByRole("button", { name: "Previous request" }));
    expect(onNavigate).toHaveBeenLastCalledWith("previous");
    onNavigate.mockClear();
    await user.click(screen.getByRole("tab", { name: "Request" }));
    // Future editors/feedback inputs within the body must keep their normal keys.
    const body = screen.getByText(/hello/).closest("pre");
    const input = document.createElement("textarea");
    input.setAttribute("aria-label", "Body note");
    body.appendChild(input);
    await user.type(input, "jk{ArrowDown}{ArrowUp}{ArrowLeft}{ArrowRight}");
    expect(input.value).toBe("jk");
    body.setAttribute("contenteditable", "true");
    fireEvent.keyDown(body, { key: "ArrowRight" });
    expect(onNavigate).not.toHaveBeenCalled();
    input.remove();
  });

  it("does not offer traversal outside the request explorer", () => {
    render(
      <RequestDetailDrawer open logId="log-detail-id" onClose={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: "Next request" })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Copy request link" }),
    ).toBeInTheDocument();
  });

  it("renders canonical snake_case details and exposes guardrail tab", async () => {
    render(
      <RequestDetailDrawer open logId="log-detail-id" onClose={vi.fn()} />,
    );

    expect(screen.getByText("789 ms")).toBeInTheDocument();
    expect(screen.getByText("30 in / 40 out")).toBeInTheDocument();
    expect(screen.getByText("Strategy")).toBeInTheDocument();
    expect(screen.getByText(/cost/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Guardrails" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Request" }));
    expect(screen.getByText(/hello/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Headers" }));
    expect(screen.getByText("Request headers")).toBeInTheDocument();
    expect(screen.getAllByText("x-test")).toHaveLength(2);

    await userEvent.click(screen.getByRole("tab", { name: "Guardrails" }));
    expect(screen.getByText(/warned/)).toBeInTheDocument();
    expect(screen.getByText("pii")).toBeInTheDocument();
    expect(screen.getByText("Latency: 12ms")).toBeInTheDocument();
  });

  it("shows billing and auto routing on the Overview tab", () => {
    extraDetail.current = {
      metadata: {
        billing_mode: "managed",
        cost_base: "0.001905",
        cost_markup_percent: "5",
        cost_source: "catalog",
        auto_requested: "auto",
        auto_model: "gpt-4o-mini",
        auto_task_tier: "simple",
        auto_complexity: "4",
        auto_preference: "cost",
        auto_eligible: "3",
        auto_considered: "9",
        auto_decision: "{truncated",
      },
    };
    render(
      <RequestDetailDrawer open logId="log-detail-id" onClose={vi.fn()} />,
    );

    expect(
      screen.getByRole("heading", { name: "Billing" }),
    ).toBeInTheDocument();
    // In the summary strip and the Billing block, by the mode's one name.
    expect(screen.getByText("Billing mode").parentElement).toHaveTextContent(
      /^Billing mode\s*Future AGI credits$/,
    );
    expect(screen.getByText("Mode").parentElement).toHaveTextContent(
      /^Mode\s*Future AGI credits$/,
    );
    // One amount, the charge; no provider cost or markup row beside it.
    expect(screen.getByText("Billed").parentElement).toHaveTextContent(
      "$0.002",
    );
    expect(screen.queryByText("Markup")).toBeNull();
    expect(screen.queryByText("Provider cost")).toBeNull();
    expect(screen.getByText("Auto routing")).toBeInTheDocument();
    expect(screen.getByText("Task").parentElement).toHaveTextContent(
      "simple (complexity 4)",
    );
    // A truncated decision document costs the ranking, not the block.
    expect(
      screen.getByText("The full ranking for this request could not be read."),
    ).toBeInTheDocument();
  });

  // Prose "Label: value" lines, raw enums and the
  // playground key's internal name, where the key and provider drawers use
  // label and value columns.
  it("translates raw values and lays Overview out in label and value columns", () => {
    extraDetail.current = {
      metadata: {
        billing_mode: "managed",
        cost_source: "catalog_max",
        auth_key_name: "__playground_4522bd5f_ee3c5664__",
      },
    };
    const { baseElement } = render(
      <RequestDetailDrawer open logId="log-detail-id" onClose={vi.fn()} />,
    );
    extraDetail.current = {};

    const text = baseElement.textContent;
    expect(text).not.toMatch(/N\/A/);
    expect(text).not.toMatch(/catalog_max/);
    expect(text).not.toMatch(/__playground_/);
    expect(
      screen.getByText("Catalog price, highest of the model's providers"),
    ).toBeInTheDocument();
    expect(screen.getByText("Console Try it")).toBeInTheDocument();

    // Label and value columns: each label is its own element, with no colon
    // and no bold prose.
    for (const label of [
      "Strategy",
      "Resolved model",
      "API key",
      "User",
      "Session",
      "Mode",
      "Price source",
    ]) {
      expect(screen.getByText(label).parentElement, label).toHaveStyle({
        display: "grid",
      });
    }
    expect(baseElement.querySelectorAll("strong")).toHaveLength(0);
  });
});

// The Metadata tab lists the request's metadata, but never the gateway's
// split of a credits charge: cost_base (the provider's part) and
// cost_markup_percent. The request's cost is the one number the Billing block
// shows.
describe("RequestDetailDrawer Metadata tab", () => {
  const CALLER = { application: "support-bot", team: "billing-ops" };

  afterEach(() => {
    extraDetail.current = {};
  });

  async function openMetadata(metadata) {
    extraDetail.current = { metadata };
    const view = render(
      <RequestDetailDrawer open logId="log-detail-id" onClose={vi.fn()} />,
    );
    await userEvent.click(screen.getByRole("tab", { name: "Metadata" }));
    return view;
  }

  it("leaves a credits request's cost split out", async () => {
    const metadata = { ...creditsLog().metadata, ...CALLER };
    const { baseElement } = await openMetadata(metadata);

    const table = screen.getByRole("table");
    expect(within(table).getByText("application")).toBeInTheDocument();
    expect(within(table).getByText("support-bot")).toBeInTheDocument();
    expect(within(table).getByText("team")).toBeInTheDocument();
    expect(within(table).getByText("billing_mode")).toBeInTheDocument();
    expect(within(table).getByText("cost_source")).toBeInTheDocument();

    const text = baseElement.textContent;
    expect(text).not.toMatch(/cost_markup_percent|cost_base/);
    expect(table.textContent).not.toMatch(/%/);
    expect(text).not.toContain(metadata.cost_base);
    expect(text).not.toContain(CREDITS.upstream);
  });

  it("leaves an own-key request's cost_base out too", async () => {
    const metadata = { ...ownKeyLog().metadata, ...CALLER };
    const { baseElement } = await openMetadata(metadata);

    const table = screen.getByRole("table");
    expect(within(table).getByText("application")).toBeInTheDocument();
    expect(within(table).getByText("billing_mode")).toBeInTheDocument();
    expect(baseElement.textContent).not.toMatch(
      /cost_markup_percent|cost_base/,
    );
    expect(baseElement.textContent).not.toContain(metadata.cost_base);
  });

  it("says there is no metadata when the split was all there was", async () => {
    const { cost_base, cost_markup_percent } = creditsLog().metadata;
    await openMetadata({ cost_base, cost_markup_percent });

    expect(
      screen.getByText("No metadata on this request."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });
});

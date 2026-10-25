import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import AttemptsTimeline, {
  AttemptsTimeline as NamedAttemptsTimeline,
} from "./AttemptsTimeline";

// One request's attempts as the detail endpoint returns them: a
// managed primary that hit a 429, a retry that timed out, then a BYOK
// failover that served the request.
const ATTEMPTS = [
  {
    attempt_id: "req-1.1",
    index: 1,
    kind: "primary",
    reason: "",
    provider: "openrouter",
    model: "anthropic/claude-sonnet-5.5",
    endpoint_id: "anthropic",
    credential_type: "managed",
    status_code: 429,
    outcome: "error",
    error_class: "rate_limited",
    error_origin: "upstream",
    error_message: "Rate limit exceeded",
    latency_ms: 120,
    ttfb_ms: null,
    prompt_tokens: 0,
    completion_tokens: 0,
    cached_tokens: 0,
    cache_write_tokens: 0,
    reasoning_tokens: 0,
    upstream_cost_micros: 0,
    billed_cost_micros: 0,
    cost_source: "unpriced",
    usage_estimated: false,
    billable: false,
  },
  {
    attempt_id: "req-1.2",
    index: 2,
    kind: "retry",
    reason: "status_429",
    provider: "openrouter",
    model: "anthropic/claude-sonnet-5.5",
    endpoint_id: "anthropic",
    credential_type: "managed",
    status_code: 504,
    outcome: "timeout",
    error_class: "timeout",
    error_origin: "gateway",
    error_message: "",
    latency_ms: 3000,
    ttfb_ms: null,
    prompt_tokens: 0,
    completion_tokens: 0,
    cached_tokens: 0,
    cache_write_tokens: 0,
    reasoning_tokens: 0,
    upstream_cost_micros: 0,
    billed_cost_micros: 0,
    cost_source: "unpriced",
    usage_estimated: false,
    billable: false,
  },
  {
    attempt_id: "req-1.3",
    index: 3,
    kind: "failover",
    reason: "timeout",
    provider: "openai",
    model: "gpt-5",
    endpoint_id: "",
    credential_type: "byok",
    status_code: 200,
    outcome: "success",
    error_class: "",
    error_origin: "",
    error_message: "",
    latency_ms: 840,
    ttfb_ms: 210,
    prompt_tokens: 1200,
    completion_tokens: 340,
    cached_tokens: 300,
    cache_write_tokens: 0,
    reasoning_tokens: 120,
    upstream_cost_micros: 1200,
    billed_cost_micros: 1260,
    cost_source: "provider_reported",
    usage_estimated: false,
    billable: true,
  },
];

const items = () =>
  within(screen.getByRole("list", { name: "Attempts" })).getAllByRole(
    "listitem",
  );

describe("AttemptsTimeline", () => {
  it("renders three attempts in index order with their kind chips", () => {
    // Given out of order, shown by index.
    render(
      <AttemptsTimeline attempts={[ATTEMPTS[2], ATTEMPTS[0], ATTEMPTS[1]]} />,
    );

    expect(
      screen.getByRole("heading", { name: "Attempts (3)" }),
    ).toBeInTheDocument();
    const rows = items();
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText("#1")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Primary")).toBeInTheDocument();
    expect(within(rows[1]).getByText("#2")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Retry")).toBeInTheDocument();
    expect(within(rows[2]).getByText("#3")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Failover")).toBeInTheDocument();
    expect(within(rows[0]).queryByText("Retry")).not.toBeInTheDocument();
  });

  it("shows target, credential, status, error, reason, timing, tokens and costs", () => {
    render(<AttemptsTimeline attempts={ATTEMPTS} />);
    const [first, second, third] = items();

    expect(first).toHaveTextContent("openrouter / anthropic/claude-sonnet-5.5");
    expect(first).toHaveTextContent("Endpoint anthropic");
    expect(within(first).getByText("Managed")).toBeInTheDocument();
    expect(first).toHaveTextContent("429");
    expect(within(first).getByText("Error")).toBeInTheDocument();
    expect(first).toHaveTextContent("Rate limited (upstream)");
    expect(first).toHaveTextContent("Rate limit exceeded");
    expect(first).toHaveTextContent("120ms");

    expect(within(second).getByText("Timed out")).toBeInTheDocument();
    expect(second).toHaveTextContent("Timeout (gateway)");
    expect(second).toHaveTextContent("Reason: Rate limited (429)");

    expect(third).toHaveTextContent("openai / gpt-5");
    expect(third).not.toHaveTextContent("Endpoint");
    expect(within(third).getByText("BYOK")).toBeInTheDocument();
    expect(third).toHaveTextContent("Reason: Timeout");
    expect(third).toHaveTextContent("TTFB 210ms");
    expect(third).toHaveTextContent("Total 840ms");
    expect(third).toHaveTextContent(
      "1,200 in (300 cached) · 340 out (120 reasoning)",
    );
    expect(third).toHaveTextContent("Upstream $0.00120");
    expect(third).toHaveTextContent("Billed $0.00126");
  });

  it("marks each attempt billed or not billed", () => {
    render(<AttemptsTimeline attempts={ATTEMPTS} />);
    const [first, second, third] = items();
    expect(within(first).getByText("Not billed")).toBeInTheDocument();
    expect(within(second).getByText("Not billed")).toBeInTheDocument();
    expect(within(third).getByText("Billed")).toBeInTheDocument();
    expect(within(third).queryByText("Not billed")).not.toBeInTheDocument();
  });

  it("marks a discarded hedge, billed or not, apart from the winning leg", () => {
    const hedge = (index, overrides) => ({
      ...ATTEMPTS[2],
      attempt_id: `req-2.${index}`,
      index,
      kind: "hedge",
      reason: "",
      ...overrides,
    });
    render(
      <AttemptsTimeline
        attempts={[
          hedge(1, { outcome: "success", billable: true }),
          // A loser upstream charged for: still billed.
          hedge(2, {
            outcome: "discarded",
            status_code: 0,
            billable: true,
            usage_estimated: true,
          }),
          // A loser cancelled before upstream reported usage.
          hedge(3, {
            outcome: "discarded",
            status_code: 0,
            billable: false,
            upstream_cost_micros: 0,
            billed_cost_micros: 0,
          }),
        ]}
      />,
    );
    const [winner, billedLoser, freeLoser] = items();

    expect(within(winner).getByText("Hedge")).toBeInTheDocument();
    expect(within(winner).getByText("Success")).toBeInTheDocument();
    expect(winner).toHaveAttribute("data-outcome", "success");

    expect(within(billedLoser).getByText("Hedge")).toBeInTheDocument();
    expect(within(billedLoser).getByText("Discarded")).toBeInTheDocument();
    expect(within(billedLoser).getByText("Billed")).toBeInTheDocument();
    expect(billedLoser).toHaveTextContent("Estimated usage");
    expect(billedLoser).toHaveAttribute("data-outcome", "discarded");

    expect(within(freeLoser).getByText("Discarded")).toBeInTheDocument();
    expect(within(freeLoser).getByText("Not billed")).toBeInTheDocument();
    expect(freeLoser).toHaveAttribute("data-outcome", "discarded");
  });

  it("shows a skipped attempt with no timing bar and no cost", () => {
    render(
      <AttemptsTimeline
        attempts={[
          {
            ...ATTEMPTS[0],
            kind: "failover",
            reason: "deadline",
            outcome: "skipped",
            status_code: 0,
            error_class: "",
            error_origin: "",
            error_message: "",
            latency_ms: null,
            ttfb_ms: null,
          },
        ]}
      />,
    );
    const [row] = items();
    expect(within(row).getByText("Skipped")).toBeInTheDocument();
    expect(within(row).getByText("Not sent")).toBeInTheDocument();
    expect(row).toHaveTextContent("Reason: Deadline reached");
    expect(row).not.toHaveTextContent("NaN");
    expect(row).not.toHaveTextContent("Upstream");
    expect(row).not.toHaveTextContent("Total");
    expect(row.querySelector("[data-latency-bar]")).toBeNull();
  });

  it("scales latency bars to the slowest attempt", () => {
    render(<AttemptsTimeline attempts={ATTEMPTS} />);
    const bars = items().map((row) => row.querySelector("[data-latency-bar]"));
    expect(bars[1].style.width).toBe("100%");
    expect(bars[2].style.width).toBe("28%");
    const ttfb = items()[2].querySelector("[data-ttfb-bar]");
    expect(ttfb.style.width).toBe("25%");
  });

  it("never shows landed cost, even when a staff response carries it", () => {
    render(
      <AttemptsTimeline
        attempts={[
          {
            ...ATTEMPTS[2],
            landed_cost_micros: 987654,
            upstream_fee_bps: 550,
            processing_fee_bps: 320,
          },
        ]}
      />,
    );
    const [row] = items();
    expect(row).not.toHaveTextContent(/landed/i);
    expect(row).not.toHaveTextContent("$0.98");
    expect(row).not.toHaveTextContent("550");
  });

  it("labels values it does not know by their raw value", () => {
    render(
      <AttemptsTimeline
        attempts={[
          { ...ATTEMPTS[0], kind: "speculative", error_class: "brand_new" },
        ]}
      />,
    );
    const [row] = items();
    expect(within(row).getByText("speculative")).toBeInTheDocument();
    expect(row).toHaveTextContent("brand_new (upstream)");
  });

  it.each([
    ["an empty list", []],
    ["no list", undefined],
    ["a non-list", "nope"],
  ])("renders nothing for %s", (_label, attempts) => {
    const { container } = render(<AttemptsTimeline attempts={attempts} />);
    expect(container).toBeEmptyDOMElement();
  });

  it.each([
    ["an empty list", []],
    ["null", null],
    ["no prop", undefined],
  ])("renders nothing for %s", (_label, attempts) => {
    const { container } = render(<AttemptsTimeline attempts={attempts} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("heading", { name: /^Attempts/ })).toBeNull();
  });

  it("is exported by name as well as by default", () => {
    expect(NamedAttemptsTimeline).toBe(AttemptsTimeline);
  });
});

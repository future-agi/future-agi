import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import { BillingSection } from "../RequestRoutingDetails";
import { AttemptsTimeline } from "../attempts/AttemptsTimeline";
import {
  CREDITS,
  OWN_KEY,
  creditsLog,
  microsOf,
  ownKeyLog,
} from "./priceFixtures";

// Price reconciliation, request details: the drawer's Billing block and each
// attempt show the one amount the org pays on the request's route, never a
// provider cost, a margin or a percentage beside it.

const NO_SPLIT = /provider cost|upstream|markup|%/i;

function billingText(log) {
  const { container } = render(<BillingSection log={log} />);
  return container.textContent;
}

// The one "Cost $x" an attempt row shows, as exact micro-dollars.
function costMicros(item) {
  const costs = item.textContent.match(/Cost \$[\d.,]+/g) || [];
  expect(costs).toHaveLength(1);
  return microsOf(costs[0].slice("Cost ".length));
}

function attempt(overrides) {
  return {
    attempt_id: "req-1.1",
    index: 1,
    kind: "primary",
    reason: "",
    provider: "openrouter",
    model: "openai/gpt-5",
    endpoint_id: "",
    credential_type: "managed",
    status_code: 200,
    outcome: "success",
    error_class: "",
    error_origin: "",
    error_message: "",
    latency_ms: 812,
    ttfb_ms: 200,
    prompt_tokens: 1000,
    completion_tokens: 2000,
    cost_source: "catalog",
    usage_estimated: false,
    billable: true,
    ...overrides,
  };
}

describe("request details price reconciliation", () => {
  it("bills a credits request its charged amount, with no breakdown", () => {
    const text = billingText(creditsLog());
    expect(screen.getByText("Billed").parentElement).toHaveTextContent(
      CREDITS.charged,
    );
    expect(screen.getByTitle("0.042 USD")).toHaveTextContent(CREDITS.charged);
    expect(text).not.toMatch(NO_SPLIT);
    expect(screen.queryByText(CREDITS.upstream)).toBeNull();
    expect(screen.queryByText(CREDITS.margin)).toBeNull();
  });

  it("costs an own-key request its provider price", () => {
    const text = billingText(ownKeyLog());
    expect(screen.getByText("Cost").parentElement).toHaveTextContent(
      `${OWN_KEY.charged} (using your own provider key)`,
    );
    expect(text).not.toMatch(NO_SPLIT);
  });

  it("shows each attempt's charged amount on credits and its provider price on an own key", () => {
    render(
      <AttemptsTimeline
        attempts={[
          attempt({
            upstream_cost_micros: CREDITS.upstreamMicros,
            billed_cost_micros: CREDITS.chargedMicros,
          }),
          attempt({
            attempt_id: "req-1.2",
            index: 2,
            kind: "failover",
            provider: "openai",
            model: "gpt-4o",
            credential_type: "byok",
            upstream_cost_micros: OWN_KEY.upstreamMicros,
            billed_cost_micros: OWN_KEY.chargedMicros,
          }),
        ]}
      />,
    );
    const [credits, own] = within(
      screen.getByRole("list", { name: "Attempts" }),
    ).getAllByRole("listitem");
    expect(credits).toHaveTextContent(`Cost ${CREDITS.charged}`);
    expect(own).toHaveTextContent(`Cost ${OWN_KEY.charged}`);
    for (const item of [credits, own]) {
      expect(item.textContent).not.toMatch(/upstream \$|markup|billed \$/i);
    }
    expect(credits).not.toHaveTextContent(`${CREDITS.upstream} `);
  });

  // A native capability attempt on an own key records the provider's price
  // as its upstream cost and bills nothing (agentcc-gateway
  // internal/capability/attempts.go:94-97, pinned by echo_test.go:75-86).
  it("costs an own-key attempt billed nothing at its provider price", () => {
    render(
      <AttemptsTimeline
        attempts={[
          attempt({
            provider: "openai",
            model: "gpt-4o",
            credential_type: "byok",
            upstream_cost_micros: 1000,
            billed_cost_micros: 0,
          }),
        ]}
      />,
    );
    const [own] = within(
      screen.getByRole("list", { name: "Attempts" }),
    ).getAllByRole("listitem");
    expect(costMicros(own)).toBe(1000);
    expect(own.textContent).not.toMatch(/upstream \$|markup|billed \$/i);
  });

  it("keeps a credits attempt charged nothing at nothing, never its provider cost", () => {
    render(
      <AttemptsTimeline
        attempts={[
          attempt({
            upstream_cost_micros: CREDITS.upstreamMicros,
            billed_cost_micros: 0,
          }),
        ]}
      />,
    );
    const [credits] = within(
      screen.getByRole("list", { name: "Attempts" }),
    ).getAllByRole("listitem");
    expect(costMicros(credits)).toBe(0);
    expect(credits).not.toHaveTextContent(CREDITS.upstream);
  });
});

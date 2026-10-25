import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import { AutoRoutingSection, BillingSection } from "./RequestRoutingDetails";
import { parseAutoDecision } from "./autoDecision";

const DECISION = {
  model: "anthropic/claude-sonnet-5.5",
  provider: "openrouter",
  task_tier: "complex",
  complexity: 34,
  preference: "balanced",
  needs: ["tools", "structured output"],
  considered: 40,
  ranked: [
    {
      model: "anthropic/claude-sonnet-5.5",
      provider: "openrouter",
      tier: 0.912,
      score: 0.97,
      est_cost_usd: 0.0123,
      latency_ms: 840,
      success_rate: 0.998,
    },
    {
      model: "openai/gpt-5",
      provider: "openrouter",
      tier: 0.88,
      score: 0.9,
      est_cost_usd: 0.01,
    },
  ],
  rejected: [{ model: "meta-llama/llama-3-8b", reason: "no tool support" }],
  fallbacks: ["openai/gpt-5"],
};

const AUTO_META = {
  auto_requested: "auto",
  auto_model: "anthropic/claude-sonnet-5.5",
  auto_task_tier: "complex",
  auto_complexity: "34",
  auto_preference: "balanced",
  auto_eligible: "12",
  auto_considered: "40",
  auto_reason:
    "complex task (complexity 34), needs tools, structured output, balanced preference: 12 of 40 models eligible",
  auto_fallbacks: "openai/gpt-5,google/gemini-3-pro",
  auto_decision: JSON.stringify(DECISION),
};

describe("parseAutoDecision", () => {
  it("parses the JSON document", () => {
    const d = parseAutoDecision(JSON.stringify(DECISION));
    expect(d.model).toBe("anthropic/claude-sonnet-5.5");
    expect(d.ranked).toHaveLength(2);
  });

  it("returns null for malformed or non-object input", () => {
    expect(parseAutoDecision("{not json")).toBeNull();
    expect(parseAutoDecision("[1,2]")).toBeNull();
    expect(parseAutoDecision("null")).toBeNull();
    expect(parseAutoDecision(undefined)).toBeNull();
  });

  it("normalises lists that are missing or the wrong shape", () => {
    const d = parseAutoDecision(
      JSON.stringify({ model: "x", ranked: "nope", rejected: [1, null, {}] }),
    );
    expect(d.ranked).toEqual([]);
    expect(d.rejected).toEqual([{}]);
    expect(d.needs).toEqual([]);
    expect(d.fallbacks).toEqual([]);
  });
});

describe("BillingSection", () => {
  it("breaks down a managed request's cost", () => {
    render(
      <BillingSection
        log={{
          cost: "0.001260",
          metadata: {
            billing_mode: "managed",
            cost_base: "0.001200",
            cost_markup_percent: "5",
            cost_source: "provider_reported",
            managed_credits_remaining: "12.400000",
          },
        }}
      />,
    );
    expect(screen.getByText("Billing")).toBeInTheDocument();
    expect(
      screen.getByText(/Managed \(FutureAGI credit\)/),
    ).toBeInTheDocument();
    expect(screen.getByText("Provider cost:").parentElement).toHaveTextContent(
      "$0.00120",
    );
    expect(screen.getByText("Markup:").parentElement).toHaveTextContent("5%");
    expect(screen.getByText("Billed:").parentElement).toHaveTextContent(
      "$0.00126",
    );
    expect(screen.getByText(/Reported by the provider/)).toBeInTheDocument();
    expect(
      screen.getByText("Credit remaining:").parentElement,
    ).toHaveTextContent("$12.40");
  });

  it("says a BYOK request carries no markup", () => {
    render(
      <BillingSection
        log={{
          cost: "0.002",
          metadata: { billing_mode: "byok", cost_source: "catalog" },
        }}
      />,
    );
    expect(screen.getByText(/Your provider key/)).toBeInTheDocument();
    expect(screen.getByText("Cost:").parentElement).toHaveTextContent(
      "billed by your provider, no markup",
    );
    expect(screen.queryByText("Markup:")).not.toBeInTheDocument();
  });

  it("flags a managed request that could not be priced", () => {
    render(
      <BillingSection
        log={{
          metadata: { billing_mode: "managed", cost_source: "unpriced" },
        }}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "No price was found for this model",
    );
  });

  it("renders nothing without billing metadata", () => {
    const { container } = render(<BillingSection log={{ metadata: {} }} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("AutoRoutingSection", () => {
  it("shows the choice, the task and the ranking", async () => {
    render(<AutoRoutingSection log={{ metadata: AUTO_META }} />);

    expect(screen.getByText("Auto routing")).toBeInTheDocument();
    const model = screen.getByText("Model:").parentElement;
    expect(model).toHaveTextContent(
      "auto anthropic/claude-sonnet-5.5 via openrouter",
    );
    expect(screen.getByText("Task:").parentElement).toHaveTextContent(
      "complex (complexity 34)",
    );
    expect(screen.getByText("Preference:").parentElement).toHaveTextContent(
      "balanced",
    );
    expect(screen.getByText("Eligible:").parentElement).toHaveTextContent(
      "12 of 40 models considered",
    );
    expect(screen.getByText("structured output")).toBeInTheDocument();
    // Metadata's fallback list wins over the decision's.
    expect(screen.getByText("google/gemini-3-pro")).toBeInTheDocument();

    const table = screen.getByRole("table", { name: "Auto routing ranking" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("Chosen");
    expect(rows[1]).toHaveTextContent("0.91");
    expect(rows[1]).toHaveTextContent("840ms");
    expect(rows[1]).toHaveTextContent("99.8%");
    expect(rows[2]).not.toHaveTextContent("Chosen");

    await userEvent.click(
      screen.getByRole("button", { name: /Show 1 of 28 models set aside/ }),
    );
    expect(screen.getByText(/no tool support/)).toBeVisible();
  });

  it("still shows the choice when auto_decision is malformed", () => {
    render(
      <AutoRoutingSection
        log={{ metadata: { ...AUTO_META, auto_decision: "{broken" } }}
      />,
    );
    expect(screen.getByText("Model:").parentElement).toHaveTextContent(
      "anthropic/claude-sonnet-5.5",
    );
    expect(screen.getByText("Task:").parentElement).toHaveTextContent(
      "complex (complexity 34)",
    );
    expect(screen.getByText("openai/gpt-5")).toBeInTheDocument();
    expect(
      screen.queryByRole("table", { name: "Auto routing ranking" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("The full ranking for this request could not be read."),
    ).toBeInTheDocument();
  });

  it("shows the reason only for a log without the structured fields", () => {
    const { unmount } = render(
      <AutoRoutingSection log={{ metadata: AUTO_META }} />,
    );
    expect(screen.queryByText(AUTO_META.auto_reason)).not.toBeInTheDocument();
    unmount();

    render(
      <AutoRoutingSection
        log={{
          metadata: {
            auto_model: "openai/gpt-5",
            auto_reason: "simple task (complexity 3), no special needs",
          },
        }}
      />,
    );
    expect(
      screen.getByText("simple task (complexity 3), no special needs"),
    ).toBeInTheDocument();
  });

  it("renders nothing for a request that did not use auto", () => {
    const { container } = render(
      <AutoRoutingSection log={{ metadata: { billing_mode: "byok" } }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import { AutoRoutingSection } from "./RequestRoutingDetails";

// Customer screens name a
// managed call's provider "Future AGI managed". The gateway marks each
// ranked candidate, and the choice, with how a choice of it is paid for
// ("billing": managed or byok; none on the node's own credentials), so the
// drawer labels the ranking from that mark, never from the provider's name:
// the org's own OpenRouter key keeps "openrouter".

const decision = (overrides) => ({
  model: "anthropic/claude-sonnet-5.5",
  provider: "openrouter",
  billing: "managed",
  task_tier: "simple",
  complexity: 3,
  preference: "balanced",
  considered: 3,
  ranked: [
    {
      model: "anthropic/claude-sonnet-5.5",
      provider: "openrouter",
      billing: "managed",
      tier: 0.5,
      score: 0.9,
    },
    {
      model: "openai/gpt-6-sol",
      provider: "openrouter",
      billing: "byok",
      tier: 0.4,
      score: 0.8,
    },
    { model: "gpt-4o", provider: "operator", tier: 0.3, score: 0.7 },
  ],
  ...overrides,
});

const renderDecision = (d) =>
  render(
    <AutoRoutingSection
      log={{
        metadata: { auto_model: d.model, auto_decision: JSON.stringify(d) },
      }}
    />,
  );

const modelRow = () =>
  // The label, not the ranking table's Model header.
  screen.getByText("Model", { selector: "p" }).parentElement;

describe("AutoRoutingSection: managed candidates read Future AGI managed", () => {
  it("names the managed choice Future AGI managed, not its upstream", () => {
    renderDecision(decision());
    expect(modelRow()).toHaveTextContent(
      "auto anthropic/claude-sonnet-5.5 via Future AGI",
    );
    expect(modelRow()).not.toHaveTextContent("openrouter");
    expect(modelRow()).not.toHaveTextContent(/Future AGI managed/);
  });

  it("captions each ranked candidate by its billing mark", () => {
    renderDecision(decision());
    const table = screen.getByRole("table", { name: "Auto routing ranking" });
    const [, managed, ownKey, unmarked] = within(table).getAllByRole("row");
    expect(managed).toHaveTextContent("Future AGI");
    expect(managed).not.toHaveTextContent("openrouter");
    expect(managed).not.toHaveTextContent(/Future AGI managed/);
    // The org's own OpenRouter key is a provider of its own.
    expect(ownKey).toHaveTextContent("openrouter");
    expect(ownKey).not.toHaveTextContent("Future AGI");
    expect(unmarked).toHaveTextContent("operator");
  });

  it("keeps the provider of an own-key choice", () => {
    renderDecision(
      decision({ model: "gpt-4o", provider: "operator", billing: "byok" }),
    );
    expect(modelRow()).toHaveTextContent("auto gpt-4o via operator");
  });

  // A gateway that records no billing marks (none on the choice, none on a
  // candidate) leaves every provider its id: no "Future AGI managed" label
  // and no billing name anywhere in the block.
  it("keeps every provider id, with no billing label, when the decision carries no marks", () => {
    const { billing: _billing, ...unmarked } = decision();
    unmarked.ranked = unmarked.ranked.map(({ billing: _b, ...rest }) => rest);
    const { container } = renderDecision(unmarked);
    expect(modelRow()).toHaveTextContent(
      "auto anthropic/claude-sonnet-5.5 via openrouter",
    );
    const table = screen.getByRole("table", { name: "Auto routing ranking" });
    const [, first, second, third] = within(table).getAllByRole("row");
    expect(first).toHaveTextContent("openrouter");
    expect(second).toHaveTextContent("openrouter");
    expect(third).toHaveTextContent("operator");
    expect(container).not.toHaveTextContent("Future AGI");
    expect(container).not.toHaveTextContent("Own key");
    expect(container).not.toHaveTextContent(/\bManaged\b/);
  });
});

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import RoutingConfigTab from "./RoutingConfigTab";

vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["gpt-4o", "gpt-4-turbo"],
}));

vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true, disabledReason: null }),
}));

const onChange = vi.fn();

const fallbackSwitch = () =>
  screen.getByRole("checkbox", { name: "Fallback Enabled" });

// Org config > Routing edits what this gateway runs for the organization's
// keys (strategy, fallback chains, complexity routing, adaptive routing,
// provider locking, the circuit breaker, retry and model timeouts). Race
// mode, scheduled completions, access groups and traffic mirroring do not
// run on this gateway: a stored value shows read-only, last on the tab, and
// is saved back unchanged.
describe("RoutingConfigTab, settings this gateway does not run", () => {
  const STORED = {
    strategy: "weighted",
    fallback_enabled: true,
    model_fallbacks: { "gpt-4o": ["gpt-4-turbo"] },
    fastest: { enabled: true, max_concurrent: 3, cancel_delay: "100ms" },
    adaptive: { enabled: true },
    retry: { enabled: true, max_retries: 2 },
    circuit_breaker: { enabled: true, failure_threshold: 5 },
    scheduled: { enabled: true },
    access_groups: { beta: { models: ["gpt-4o"] } },
    mirror: {
      enabled: true,
      rules: [
        {
          source_model: "gpt-4o",
          target_provider: "anthropic",
          target_model: "claude-sonnet-4",
          sample_rate: 0.1,
        },
      ],
    },
    complexity: { enabled: true },
    provider_lock: { enabled: true, allowed_providers: ["openai"] },
    model_timeouts: { "gpt-4o": "30s" },
  };

  beforeEach(() => onChange.mockReset());

  it("offers no race, schedule, group or mirror control; what runs stays editable", () => {
    render(<RoutingConfigTab routing={STORED} onChange={onChange} />);
    for (const title of [
      "Fastest Response (Race Mode)",
      "Scheduled Completions",
      "Model Access Groups",
      "Traffic Mirroring",
    ]) {
      expect(screen.queryByText(title), title).toBeNull();
    }
    expect(fallbackSwitch()).toBeChecked();
    expect(screen.getByRole("button", { name: "Weighted" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByRole("button", { name: /Add Fallback Chain/ }),
    ).toBeInTheDocument();
    for (const title of [
      "Complexity Routing",
      "Adaptive Routing",
      "Provider Locking",
      "Circuit Breaker",
      "Retry Configuration",
      "Model Timeouts",
    ]) {
      expect(screen.getByText(title), title).toBeInTheDocument();
    }
  });

  it("shows no stored setting this gateway does not run, and says in one line they are kept", () => {
    render(<RoutingConfigTab routing={STORED} onChange={onChange} />);
    expect(screen.queryByTestId("routing-recorded-only")).toBeNull();
    for (const text of ["up to 3 at once", "Scheduled completions", "beta"]) {
      expect(screen.queryByText(new RegExp(text))).toBeNull();
    }
    expect(screen.getByTestId("routing-stored-not-run")).toHaveTextContent(
      "This configuration also stores routing settings this gateway does not run (default model, conditional or scheduled routes, race mode). A save keeps them unchanged.",
    );
  });

  it("keeps every stored setting on save", async () => {
    render(<RoutingConfigTab routing={STORED} onChange={onChange} />);
    await userEvent.click(fallbackSwitch());
    const next = onChange.mock.calls.at(-1)[0];
    expect(next).toEqual({ ...STORED, fallback_enabled: false });
  });

  it("shows no read-only block when only settings that run are stored", () => {
    render(
      <RoutingConfigTab
        routing={{
          strategy: "least_latency",
          fallback_enabled: true,
          model_fallbacks: { a: ["b"] },
          retry: { enabled: true },
        }}
        onChange={onChange}
      />,
    );
    expect(screen.queryByTestId("routing-stored-not-run")).toBeNull();
  });
});

// The strategy picker sits in a card that clips what runs past its edge.
// At phone width its options wrap onto more lines, so every strategy stays
// on screen and can be picked.
describe("RoutingConfigTab, Routing Strategy at phone width", () => {
  it("wraps the strategy options inside the card", () => {
    render(
      <RoutingConfigTab
        routing={{ strategy: "weighted" }}
        onChange={onChange}
      />,
    );
    const group = screen
      .getByRole("button", { name: "None: first provider by ID" })
      .closest('[role="group"]');
    expect(group).toHaveStyle({ flexWrap: "wrap" });
  });
});

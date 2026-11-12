import React, { useState } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import RoutingConfigTab from "./RoutingConfigTab";

let availableModels = ["gpt-4o", "gpt-4-turbo"];
vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => availableModels,
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
      screen.getByRole("button", { name: /Add fallback chain/i }),
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

describe("BP-14 org routing chain editing", () => {
  // eslint-disable-next-line react/prop-types
  const Draft = ({ chains }) => {
    const [routing, setRouting] = useState({ model_fallbacks: chains });
    return (
      <RoutingConfigTab
        routing={routing}
        onChange={(next) => {
          onChange(next);
          setRouting(next);
        }}
      />
    );
  };
  beforeEach(() => {
    availableModels = ["A", "B", "C"];
    onChange.mockReset();
  });

  it("cannot rename A to an occupied B or replace B's chain", async () => {
    render(<Draft chains={{ A: ["B"], B: ["C"] }} />);
    await userEvent.click(
      screen.getAllByRole("combobox", { name: "Primary" })[0],
    );
    expect(screen.getByRole("option", { name: /B/ })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    await userEvent.keyboard("{Escape}");
    expect(onChange).not.toHaveBeenCalled();
    await userEvent.click(fallbackSwitch());
    expect(onChange.mock.calls.at(-1)[0].model_fallbacks).toEqual({
      A: ["B"],
      B: ["C"],
    });
  });

  it("adds only unused primaries, then disables Add with an explanation", async () => {
    render(<Draft chains={{ A: ["B"], B: ["C"] }} />);
    const add = screen.getByRole("button", { name: /Add fallback chain/i });
    await userEvent.click(add);
    expect(onChange.mock.calls.at(-1)[0].model_fallbacks).toEqual({
      A: ["B"],
      B: ["C"],
      C: [],
    });
    expect(add).toBeDisabled();
    expect(
      screen.getByText(/Every available model already has a chain/),
    ).toBeInTheDocument();
    add.click();
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("never puts a newly blank backup into the parent save payload", async () => {
    render(<Draft chains={{ A: ["B"] }} />);
    const add = screen.getByRole("button", { name: "Add fallback" });
    expect(add).toBeDisabled();
    expect(screen.getByText(/Only added models are saved/)).toBeInTheDocument();
    add.click();
    expect(onChange).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("combobox", { name: "New fallback for A" }),
    );
    await userEvent.click(screen.getByRole("option", { name: "C" }));
    await userEvent.click(add);
    expect(onChange.mock.calls.at(-1)[0].model_fallbacks).toEqual({
      A: ["B", "C"],
    });
    expect(add).toBeDisabled();
  });

  it("preserves unknown and over-limit backups without sorting, trimming or replacing them", async () => {
    const backups = Array.from({ length: 17 }, (_, i) => `stored-${i}`);
    render(<Draft chains={{ "stored-primary": backups }} />);
    expect(screen.getByRole("combobox", { name: "Primary" })).toHaveTextContent(
      "stored-primary",
    );
    expect(
      screen.getByRole("combobox", { name: "Fallback 17" }),
    ).toHaveTextContent("stored-16");
    expect(screen.getByRole("button", { name: "Add fallback" })).toBeDisabled();
    await userEvent.click(fallbackSwitch());
    expect(onChange.mock.calls.at(-1)[0].model_fallbacks).toEqual({
      "stored-primary": backups,
    });
  });

  it("renames to a free primary without changing backup order or adjacent chains", async () => {
    availableModels = ["A", "B", "C", "D"];
    render(<Draft chains={{ A: ["C", "B"], B: ["D"] }} />);
    await userEvent.click(
      screen.getAllByRole("combobox", { name: "Primary" })[0],
    );
    await userEvent.click(screen.getByRole("option", { name: "D" }));
    expect(
      Object.entries(onChange.mock.calls.at(-1)[0].model_fallbacks),
    ).toEqual([
      ["D", ["C", "B"]],
      ["B", ["D"]],
    ]);
  });
});

describe("R3-CONTROLS-02 complexity tier identities", () => {
  it("adds to a non-contiguous map without replacing a surviving target", async () => {
    const tiers = {
      tier_1: { max_score: 20, model: "A", provider: "provider-a" },
      tier_3: { max_score: 90, model: "B", provider: "provider-b" },
    };
    onChange.mockReset();
    render(<RoutingConfigTab routing={{ complexity: { enabled: true, tiers } }} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /Complexity Routing/ }));
    await userEvent.click(screen.getByRole("button", { name: "Add Tier" }));
    const next = onChange.mock.calls.at(-1)[0].complexity.tiers;
    expect(Object.keys(next)).toHaveLength(3);
    expect(next.tier_1).toEqual(tiers.tier_1);
    expect(next.tier_3).toEqual(tiers.tier_3);
    expect(next.tier_2).toEqual({ max_score: 50, model: "", provider: "" });
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

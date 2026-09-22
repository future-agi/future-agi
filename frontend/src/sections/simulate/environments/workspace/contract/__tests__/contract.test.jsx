import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import RlContractPanel from "../RlContractPanel";

// A voice environment with two tools (one read, one write by verb heuristic),
// two hard rules and a seeded table — enough to exercise the world (capability
// graph), the internals (schema + tool code + check code) and the run-end card.
const voiceEnv = {
  name: "Refund Copilot",
  surface: "voice",
  tools: [
    { name: "lookup_account", desc: "Reads the caller's account." },
    { name: "issue_refund", desc: "Refunds a charge." },
  ],
  rules: ["Never disclose another caller's data.", "Only refund verified callers."],
  seed: { tables: [{ name: "customers", rows: 240, note: "40 with saved cards" }] },
};

const renderPanel = (props = {}) =>
  render(
    <RlContractPanel
      env={voiceEnv}
      envState={{ evals: [{ id: "task_success" }], agent: { name: "Support agent" } }}
      patch={vi.fn()}
      onGo={vi.fn()}
      {...props}
    />,
  );

describe("RlContractPanel", () => {
  it("leads with the environment-contract heading and blurb", () => {
    renderPanel();
    expect(screen.getByText("Environment contract")).toBeInTheDocument();
    expect(screen.getByText(/What this environment is made of/)).toBeInTheDocument();
  });

  it("renders the world-internals cards: schema, tool code and check code", () => {
    renderPanel();
    expect(screen.getByText("World state · database schema")).toBeInTheDocument();
    expect(screen.getByText("Tool implementations")).toBeInTheDocument();
    expect(screen.getByText("Check implementations")).toBeInTheDocument();
  });

  it("renders the run end conditions — terminate, truncate, clock and seed", () => {
    renderPanel();
    expect(screen.getByText("Run end conditions")).toBeInTheDocument();
    expect(screen.getByText("Terminate")).toBeInTheDocument();
    expect(screen.getByText("Truncate")).toBeInTheDocument();
    expect(screen.getByText(/^Clock ·/)).toBeInTheDocument();
    expect(screen.getByText("Deterministic seed")).toBeInTheDocument();
  });

  it("classifies tool effects by verb heuristic (read-only vs writes), marked inferred", () => {
    renderPanel();
    // lookup_* → read-only; refund → writes; neither overridden yet → "· inferred".
    expect(screen.getByText("read-only · inferred")).toBeInTheDocument();
    expect(screen.getByText("writes · inferred")).toBeInTheDocument();
  });

  it("records a tool-effect override on the Contract tab via patch (singular kind)", () => {
    const patch = vi.fn();
    renderPanel({ patch });
    // Open the read tool's effect picker and switch it to Writes data.
    fireEvent.click(screen.getByText("read-only · inferred"));
    fireEvent.click(screen.getByText("Writes data"));
    expect(patch).toHaveBeenCalledTimes(1);
    expect(patch.mock.calls[0][0]).toEqual({
      toolResolutions: { lookup_account: "write" },
    });
  });

  it("mounts the actors panel in place of the deferred empty state", () => {
    renderPanel();
    expect(screen.getAllByText("Colleague with a different plan").length).toBeGreaterThan(0);
    expect(screen.queryByText("Actors land with the next phase")).toBeNull();
  });

  describe("template lock (read-only until forked)", () => {
    const LOCK_TOOLTIP = "Fork this environment to edit.";

    it("renders the tool-effect chip as a static read-out — no override menu, no patch", () => {
      const patch = vi.fn();
      renderPanel({ patch, locked: true });

      // The chip still shows the effect, but clicking it opens nothing.
      fireEvent.click(screen.getByText("read-only · inferred"));
      expect(screen.queryByText("Writes data")).toBeNull();
      expect(patch).not.toHaveBeenCalled();

      // The locked chip carries the fork tooltip (one per tool).
      expect(screen.getAllByLabelText(LOCK_TOOLTIP).length).toBeGreaterThan(0);
    });

    it("keeps the override menu interactive when not locked", () => {
      const patch = vi.fn();
      renderPanel({ patch, locked: false });

      fireEvent.click(screen.getByText("read-only · inferred"));
      expect(screen.getByText("Writes data")).toBeInTheDocument();
      expect(screen.queryByLabelText(LOCK_TOOLTIP)).toBeNull();
    });
  });
});

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import RlContractPanel from "../RlContractPanel";

// A voice environment with two tools, two hard rules and a seeded table —
// enough to exercise the world (capability graph), the internals (tables +
// tools) and the run-end card.
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

  it("renders the world-internals cards over real data only: tables and tools", () => {
    renderPanel();
    expect(screen.getByText("World state · database")).toBeInTheDocument();
    expect(screen.getAllByText("Tools").length).toBeGreaterThan(0);
    // The synthesised handler/check source cards are gone: their bodies were
    // generated from the env manifest by _fixtures/envInternals, not read from
    // the job.
    expect(screen.queryByText("Tool implementations")).toBeNull();
    expect(screen.queryByText("Check implementations")).toBeNull();
  });

  it("renders the run end conditions — terminate, truncate, clock and seed", () => {
    renderPanel();
    expect(screen.getByText("Run end conditions")).toBeInTheDocument();
    expect(screen.getByText("Terminate")).toBeInTheDocument();
    expect(screen.getByText("Truncate")).toBeInTheDocument();
    expect(screen.getByText(/^Clock ·/)).toBeInTheDocument();
    expect(screen.getByText("Deterministic seed")).toBeInTheDocument();
  });

  it("lists each declared tool with its description and no inferred effect chip", () => {
    renderPanel();
    expect(screen.getAllByText("lookup_account").length).toBeGreaterThan(0);
    expect(screen.getAllByText("issue_refund").length).toBeGreaterThan(0);
    expect(screen.getByText("Reads the caller's account.")).toBeInTheDocument();
    // The read-only/writes classification came from a verb heuristic over the
    // tool name, so the chip and its override picker were dropped.
    expect(screen.queryByText("read-only · inferred")).toBeNull();
    expect(screen.queryByText("writes · inferred")).toBeNull();
    expect(screen.queryByText("Writes data")).toBeNull();
  });

  it("shows where a tool runs only when ALK reported an entrypoint for it", () => {
    renderPanel({
      env: {
        ...voiceEnv,
        toolEntrypoints: [
          { tool: "issue_refund", module: "billing", callable: "refund" },
        ],
      },
    });
    expect(screen.getByText("billing.refund")).toBeInTheDocument();
    // lookup_account has no entrypoint, so it carries no invented location.
    expect(screen.queryByText(/lookup_account\./)).toBeNull();
  });

  it("no longer mounts the dummy Actors section (commented out, to be picked up later)", () => {
    renderPanel();
    expect(screen.queryByText("Colleague with a different plan")).toBeNull();
  });

  describe("template lock (read-only until forked)", () => {
    const LOCK_TOOLTIP = "Fork this environment to edit.";

    it("renders the tool list as a static read-out — no override menu, no patch", () => {
      const patch = vi.fn();
      renderPanel({ patch, locked: true });

      // With the effect override gone there is nothing on this card to lock,
      // and nothing that can reach patch.
      expect(screen.getAllByText("lookup_account").length).toBeGreaterThan(0);
      expect(screen.queryByText("Writes data")).toBeNull();
      expect(screen.queryByLabelText(LOCK_TOOLTIP)).toBeNull();
      expect(patch).not.toHaveBeenCalled();
    });
  });
});

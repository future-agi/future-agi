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
  it("opens on its content, with no heading repeating the tab", () => {
    renderPanel();
    expect(screen.queryByText("Environment contract")).toBeNull();
    expect(screen.queryByText(/What this environment is made of/)).toBeNull();
  });

  it("no longer holds the world's tables, tools or capability graph: they live on World", () => {
    renderPanel();
    expect(screen.queryByText(/World state/)).toBeNull();
    expect(screen.queryByText("lookup_account")).toBeNull();
    expect(screen.queryByText("Capability graph")).toBeNull();
  });

  it("renders the authored run end conditions: terminate, truncate and clock", () => {
    renderPanel({
      env: {
        ...voiceEnv,
        endConditions: {
          ended_reasons: ["customer-ended-call", "silence-timed-out"],
          max_turns: 10,
          max_duration_seconds: 3600,
          clock: "real-time",
        },
      },
    });
    expect(screen.getByText("Run end conditions")).toBeInTheDocument();
    expect(screen.getByText("Terminate")).toBeInTheDocument();
    expect(screen.getByText("customer-ended-call")).toBeInTheDocument();
    expect(screen.getByText("Truncate")).toBeInTheDocument();
    expect(screen.getByText("10 turns")).toBeInTheDocument();
    expect(screen.getByText("Clock · real-time")).toBeInTheDocument();
  });

  it("has no seed row: the API carries no seed for it to describe", () => {
    renderPanel({ env: { ...voiceEnv, endConditions: { ended_reasons: ["cancelled"] } } });
    expect(screen.queryByText("Deterministic seed")).toBeNull();
  });

  it("says a template or fork has no end conditions because it was never built", () => {
    renderPanel({ env: { ...voiceEnv, endConditions: null } });
    expect(screen.getByText("Not authored yet")).toBeInTheDocument();
    expect(screen.getByText("This environment hasn't been built, so it has none.")).toBeInTheDocument();
    expect(screen.queryByText(/The build writes these/)).toBeNull();
  });

  it("says the end conditions are not authored yet instead of showing sample ones", () => {
    renderPanel({ env: { ...voiceEnv, endConditions: null }, backed: true });
    expect(screen.getByText(/The build writes these once it has read how a run of this agent ends/)).toBeInTheDocument();
    expect(screen.getByText("Run end conditions")).toBeInTheDocument();
    expect(screen.getByText("Not authored yet")).toBeInTheDocument();
    expect(screen.queryByText("Terminate")).toBeNull();
    expect(screen.queryByText(/^Clock ·/)).toBeNull();
  });

  it("no longer mounts the dummy Actors section (commented out, to be picked up later)", () => {
    renderPanel();
    expect(screen.queryByText("Colleague with a different plan")).toBeNull();
  });

});

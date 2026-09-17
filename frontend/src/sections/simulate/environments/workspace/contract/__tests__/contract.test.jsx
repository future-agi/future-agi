import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import RlContractPanel from "../RlContractPanel";

// A voice environment with two tools (one write tool) and two hard rules —
// enough to exercise the adapter, the spaces, the reward verifiers and the
// blocking-grading gap.
const voiceEnv = {
  surface: "voice",
  tools: [
    { name: "lookup_account", desc: "Reads the caller's account." },
    { name: "issue_refund", desc: "Refunds a charge." },
  ],
  rules: ["Never disclose another caller's data.", "Only refund verified callers."],
};

const renderPanel = (props = {}) =>
  render(
    <RlContractPanel
      env={voiceEnv}
      envState={{ evals: [] }}
      onGo={vi.fn()}
      {...props}
    />,
  );

describe("RlContractPanel", () => {
  it("shows the compiled count from contractParts (reward not done until evals exist)", () => {
    renderPanel({ envState: { evals: [] } });
    // adapter, spaces, dynamics, episode done; reward waits on evals → 4/5.
    expect(screen.getByText("4/5 compiled")).toBeInTheDocument();
  });

  it("marks the reward part compiled once an eval is applied", () => {
    renderPanel({ envState: { evals: [{ id: "task_success" }] } });
    expect(screen.getByText("5/5 compiled")).toBeInTheDocument();
  });

  it("renders the adapter label the fixture resolves from the surface", () => {
    renderPanel();
    // Voice surface → the Voice adapter (bold label, exact case; the chips are
    // lowercase "voice").
    expect(screen.getByText("Voice")).toBeInTheDocument();
  });

  it("resolves the adapter from MODALITY_FOR, not a hardcode", () => {
    renderPanel({ env: { ...voiceEnv, surface: "email" } });
    // email → chat modality → the Chat adapter.
    expect(screen.getByText("Chat")).toBeInTheDocument();
  });

  it("renders observation fields including the adapter-filled ones", () => {
    renderPanel();
    expect(screen.getByText("goal")).toBeInTheDocument();
    expect(screen.getByText("audio_state")).toBeInTheDocument();
  });

  it("renders action verbs with the tool-count args", () => {
    renderPanel();
    expect(screen.getByText("call_tool")).toBeInTheDocument();
    expect(screen.getByText("name: enum[2 tools], args: dict")).toBeInTheDocument();
  });

  it("renders reward verifiers whose constraint notes are the env's hard rules", () => {
    renderPanel();
    expect(screen.getByText("task_completed")).toBeInTheDocument();
    // Each hard rule becomes a constraint verifier row.
    expect(screen.getAllByText("hard_rule_violation")).toHaveLength(2);
    expect(screen.getByText("Never disclose another caller's data.")).toBeInTheDocument();
    expect(screen.getAllByText("-1.00").length).toBeGreaterThanOrEqual(2);
  });

  it("routes 'Fix gaps' to the evals tab when the top blocking gap is Grading", () => {
    const onGo = vi.fn();
    renderPanel({ onGo, envState: { evals: [] } });
    const fix = screen.getByRole("button", { name: /Fix in Evaluations/i });
    fireEvent.click(fix);
    expect(onGo).toHaveBeenCalledWith("evals");
  });

  it("hides the gaps action once nothing is blocking", () => {
    renderPanel({ envState: { evals: [{ id: "task_success" }] } });
    expect(screen.queryByRole("button", { name: /Fix in Evaluations/i })).toBeNull();
  });

  it("mounts the actors panel in place of the deferred empty state", () => {
    renderPanel();
    expect(screen.getByText("Colleague with a different plan")).toBeInTheDocument();
    expect(screen.queryByText("Actors land with the next phase")).toBeNull();
  });
});

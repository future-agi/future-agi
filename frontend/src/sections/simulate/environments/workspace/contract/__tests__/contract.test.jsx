import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
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

  it("renders the authored run end conditions: how a conversation ends, its limits and the clock", () => {
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
    expect(screen.getByText("How a conversation can end")).toBeInTheDocument();
    expect(screen.getByText("Customer ended the conversation")).toBeInTheDocument();
    expect(screen.getByText("customer-ended-call")).toBeInTheDocument();
    expect(screen.getByText("Limits")).toBeInTheDocument();
    expect(screen.getByText("10 turns")).toBeInTheDocument();
    expect(screen.getByText("Clock · real-time")).toBeInTheDocument();
  });

  it("W10-SIMULATE-OTHER-U07 says how a run ends in plain words, with each recorded code beside it", () => {
    const canonical = [
      "assistant-ended-call",
      "cancelled",
      "customer-ended-call",
      "max-duration-reached",
      "no-answer",
      "silence-timed-out",
    ];
    renderPanel({
      env: {
        ...voiceEnv,
        endConditions: {
          ended_reasons: [...canonical, "provider_hung-up"],
          max_turns: 1,
          max_duration_seconds: 3600,
          clock: "real-time",
        },
      },
    });

    [
      "Your agent ended the conversation",
      "Cancelled",
      "Customer ended the conversation",
      "Time limit reached",
      "No answer",
      "Silence timed out",
    ].forEach((label) => expect(screen.getByText(label)).toBeInTheDocument());
    expect(screen.getByText("The simulated customer finished or dropped off.")).toBeInTheDocument();
    // The code stays beside its label: call logs show the code.
    canonical.forEach((code) => expect(screen.getByText(code)).toBeInTheDocument());
    // A code outside the known set still reads as words.
    expect(screen.getByText("Provider hung up")).toBeInTheDocument();
    expect(screen.getByText("provider_hung-up")).toBeInTheDocument();
    // Limits read as amounts, not raw seconds.
    expect(screen.getByText("1 turn")).toBeInTheDocument();
    expect(screen.getByText("1 hour")).toBeInTheDocument();
    expect(screen.queryByText("3600s")).toBeNull();
    // No RL vocabulary.
    expect(screen.queryByText("Terminate")).toBeNull();
    expect(screen.queryByText("Truncate")).toBeNull();
    expect(screen.queryAllByText(/Bootstrap from the last state|Goal verifiers settle/)).toHaveLength(0);
  });

  it("G4-UX-1 scopes the time limit to the whole run, not to each conversation", () => {
    // max_duration_seconds is the job's wall-clock ceiling: every scenario in the
    // run shares it. The max-duration-reached ending is a separate,
    // per-conversation timeout.
    renderPanel({
      env: {
        ...voiceEnv,
        endConditions: {
          ended_reasons: ["max-duration-reached"],
          max_duration_seconds: 3600,
          clock: "real-time",
        },
      },
    });

    expect(screen.getByText("How each conversation can end, and the limits on a run")).toBeInTheDocument();
    expect(screen.getByText("What stops a run early.")).toBeInTheDocument();
    expect(screen.getByText("1 hour")).toBeInTheDocument();
    expect(
      screen.getByText("The whole run, every scenario together, must finish inside this time."),
    ).toBeInTheDocument();
    expect(screen.getByText("This conversation hit its own time limit.")).toBeInTheDocument();
    // Nothing presents the run's ceiling as a per-conversation allowance.
    expect(screen.queryByText("A conversation stops when it reaches one of these.")).toBeNull();
    expect(screen.queryByText("The time limit set for this environment.")).toBeNull();
    expect(screen.queryByText("The conversation ran out of time.")).toBeNull();
    expect(screen.queryByText("How a conversation can end, and the limits that stop it")).toBeNull();
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
    expect(screen.queryByText("How a conversation can end")).toBeNull();
    expect(screen.queryByText(/^Clock ·/)).toBeNull();
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

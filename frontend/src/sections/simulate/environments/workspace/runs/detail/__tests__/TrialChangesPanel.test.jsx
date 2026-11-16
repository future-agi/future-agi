import { describe, it, expect } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import PropTypes from "prop-types";
import TrialChangesPanelView from "../TrialChangesPanel";

const EVERY_LIST = {
  state_changes: [],
  duplicates: [],
  decisions: [],
  pre_run: [],
  approvals: [],
  usage: [],
  usage_summary: [],
  notices: [],
  unreadable: false,
};
function TrialChangesPanel({ trialState }) {
  return (
    <TrialChangesPanelView
      trialState={trialState && { ...EVERY_LIST, ...trialState }}
    />
  );
}
TrialChangesPanel.propTypes = { trialState: PropTypes.object };

const CHANGE = {
  kind: "action",
  value: "approvals.change",
  label: "approvals.change is taken for them",
};

describe("TrialChangesPanel", () => {
  it("says a participant's reads apart from what it committed, and one that only read changed nothing", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [
            {
              who: "agent",
              name: "the agent",
              attempted: 2,
              committed: 0,
              refused: 0,
              faulted: 0,
              duplicate: 0,
              committed_actions: [],
              read: 2,
              read_actions: ["get_order", "list_refunds"],
              no_change: true,
            },
          ],
        }}
      />,
    );
    expect(
      screen.getByText("2 attempted · 0 committed · 2 read"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Read, changing nothing: get_order, list_refunds"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No change: it committed nothing"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^Committed:/)).toBeNull();
  });

  it("shows a call blocked outside the test world beside the refused count, never as a change", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [
            {
              who: "agent",
              name: "the agent",
              attempted: 2,
              committed: 0,
              refused: 2,
              faulted: 0,
              duplicate: 0,
              committed_actions: [],
              no_change: true,
              blocked: [
                {
                  host: "refunds.example.com",
                  method: "CONNECT",
                  tool: null,
                  count: 1,
                },
                {
                  host: "api.example.com",
                  method: "POST",
                  tool: "issue_refund",
                  count: 3,
                },
              ],
            },
          ],
        }}
      />,
    );
    expect(
      screen.getByText("2 attempted · 0 committed · 2 refused"),
    ).toBeInTheDocument();
    expect(
      screen.getAllByTestId("blocked-call").map((one) => one.textContent),
    ).toEqual([
      "Blocked: refunds.example.com (CONNECT) ×1",
      "Blocked: api.example.com (POST) ×3",
    ]);
    expect(
      screen.getByText("No change: it committed nothing"),
    ).toBeInTheDocument();
  });

  it("links the world as the trial ended when its end state was kept, and says nothing of it otherwise", () => {
    const changes = [
      {
        who: "agent",
        name: "the agent",
        attempted: 1,
        committed: 1,
        refused: 0,
        faulted: 0,
        duplicate: 0,
        committed_actions: ["issue_refund"],
      },
    ];
    const { unmount } = render(
      <TrialChangesPanel
        trialState={{
          state_changes: changes,
          end_state_url: "http://localhost:9005/bucket/end-state",
        }}
      />,
    );
    expect(
      screen.getByRole("link", { name: "download its records and action log" }),
    ).toHaveAttribute("href", "http://localhost:9005/bucket/end-state");
    unmount();
    render(<TrialChangesPanel trialState={{ state_changes: changes }} />);
    expect(screen.queryByText(/The world as this trial ended/)).toBeNull();
  });

  it("keeps attempted, refused and committed apart per participant and says what each actor chose", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [
            {
              who: "agent",
              name: "the agent",
              attempted: 3,
              committed: 2,
              refused: 1,
              faulted: 0,
              duplicate: 0,
              committed_actions: ["crm.request_approval", "crm.update_deal"],
            },
            {
              who: "manager",
              name: "Priya Raman",
              attempted: 1,
              committed: 1,
              refused: 0,
              faulted: 0,
              duplicate: 0,
              committed_actions: ["crm.request_approval"],
            },
          ],
          decisions: [
            {
              actor: "manager",
              name: "Priya Raman",
              play: "live",
              action: "crm.request_approval",
              why: "12% is within my limit",
              line: "Priya Raman · Decided · crm.request_approval · 12% is within my limit",
            },
            {
              actor: "manager",
              name: "Priya Raman",
              play: "live",
              action: null,
              why: "already approved",
              line: "Priya Raman · Decided · Did nothing · already approved",
            },
          ],
        }}
      />,
    );
    expect(
      screen.getByText("3 attempted · 2 committed · 1 refused"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Committed: crm.request_approval, crm.update_deal"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Priya Raman · Decided · crm.request_approval · 12% is within my limit",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Priya Raman · Decided · Did nothing · already approved",
      ),
    ).toBeInTheDocument();
  });

  it("says nothing changed when the trial recorded nothing", () => {
    render(
      <TrialChangesPanel trialState={{ state_changes: [], decisions: [] }} />,
    );
    expect(
      screen.getByText("Nothing changed in the world in this trial."),
    ).toBeInTheDocument();
  });
  it("names a commit the agent was told timed out, and a fallback the world refused", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [
            {
              who: "agent",
              name: "the agent",
              attempted: 2,
              committed: 2,
              committed_actions: ["apply_discount", "apply_discount"],
              answer_lost_actions: ["apply_discount"],
            },
            {
              who: "alex",
              name: "Alex Vance",
              attempted: 1,
              refused: 1,
              committed: 0,
              committed_actions: [],
            },
          ],
          decisions: [
            {
              actor: "alex",
              name: "Alex Vance",
              play: "timeout",
              fallback: CHANGE,
              outcome: "fallback_not_committed",
              action_outcome: "refused",
              line: "Alex Vance · Timed out · No answer: their fallback action was tried for them and refused by the world",
            },
            {
              actor: "alex",
              name: "Alex Vance",
              play: "timeout",
              fallback: CHANGE,
              outcome: "fallback_action",
              action_outcome: "committed",
              line: "Alex Vance · Timed out · No answer: their fallback action was taken for them",
            },
          ],
        }}
      />,
    );
    expect(
      screen.getByText("Committed but answered with a timeout: apply_discount"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Alex Vance · Timed out · No answer: their fallback action was tried for them and refused by the world",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Alex Vance · Timed out · No answer: their fallback action was taken for them",
      ),
    ).toBeInTheDocument();
  });

  it("says no change for a participant that committed nothing", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [
            {
              who: "agent",
              name: "the agent",
              attempted: 0,
              committed: 0,
              refused: 0,
              faulted: 0,
              duplicate: 0,
              committed_actions: [],
              no_change: true,
            },
          ],
        }}
      />,
    );
    expect(
      screen.getByText("No change: it committed nothing"),
    ).toBeInTheDocument();
  });

  it("shows what actors wrote before the run apart, and each approval with whether the agent waited", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [
            {
              who: "priya",
              name: "Priya",
              attempted: 1,
              committed: 1,
              refused: 0,
              faulted: 0,
              duplicate: 0,
              committed_actions: ["approve"],
            },
          ],
          decisions: [],
          pre_run: [
            {
              actor: "priya",
              name: "Priya",
              play: "history",
              action: "slack.post",
              arguments: { text: "20% for Acme?" },
              line: "Priya: 20% for Acme?",
            },
          ],
          approvals: [
            {
              request: { action: "request_discount" },
              decision: { by: "priya", name: "Priya", action: "approve" },
              before_decision: [],
              after_decision: [],
              acted_before_decision: false,
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("Priya: 20% for Acme?")).toBeInTheDocument();
    expect(
      screen.getByText(
        "request_discount, then Priya: approve · the agent waited for the decision",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Before the run · /)).toBeNull();
  });

  it("says each actor's model use per model, then the trial's in all, as the server sums them", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [
            {
              who: "priya",
              name: "Priya",
              attempted: 1,
              committed: 1,
              refused: 0,
              faulted: 0,
              duplicate: 0,
              committed_actions: ["x"],
            },
          ],
          usage_summary: [
            {
              actor: "priya",
              name: "Priya",
              model: "m1",
              calls: 2,
              tokens: 30,
              cost_usd: 0.003,
            },
            {
              actor: "ravi",
              name: "ravi",
              model: "",
              calls: 1,
              tokens: 15,
              cost_usd: 0.0005,
            },
          ],
          usage_total: { calls: 3, tokens: 45, cost_usd: 0.0035 },
        }}
      />,
    );
    expect(
      screen.getByText("Priya on m1: 2 model calls, 30 tokens, $0.0030"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("ravi: 1 model call, 15 tokens, $0.0005"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "In all: 3 model calls, 45 tokens, $0.0035, counted in the simulator's usage",
      ),
    ).toBeInTheDocument();
  });

  it("says first when a scenario fault did not take effect, even when nothing else changed", () => {
    const message =
      "The timeout fault on call 1 of crm.apply_discount could not be applied: the agent made that call outside the world's door (in its own process), so nothing held it.";
    render(
      <TrialChangesPanel
        trialState={{
          notices: [{ code: "fault_not_applied", message }, { code: "blank" }],
        }}
      />,
    );
    expect(
      screen.getByText("Worth knowing about this trial"),
    ).toBeInTheDocument();
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(
      screen.getByText("Nothing changed in the world in this trial."),
    ).toBeInTheDocument();
  });

  it("shows no notices heading when the trial has none", () => {
    render(<TrialChangesPanel trialState={{ state_changes: [] }} />);
    expect(
      screen.queryByText("Worth knowing about this trial"),
    ).not.toBeInTheDocument();
  });

  it("shows a write the world committed twice in red", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [
            {
              who: "agent",
              name: "the agent",
              attempted: 2,
              committed: 2,
              refused: 0,
              faulted: 0,
              duplicate: 0,
              committed_actions: ["issue_refund", "issue_refund"],
              answer_lost_actions: ["issue_refund"],
            },
          ],
          duplicates: [{ action: "orders.issue_refund", name: "issue_refund" }],
        }}
      />,
    );
    expect(
      screen.getByText("Committed twice: issue_refund"),
    ).toBeInTheDocument();
  });

  it("shows each changed record before and after", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [],
          decisions: [],
          records: {
            changes: [
              {
                table: "orders",
                key: [1042],
                kind: "changed",
                fields: ["status"],
                before: { id: 1042, status: "paid" },
                after: { id: 1042, status: "refunded" },
              },
              {
                table: "refunds",
                key: [1],
                kind: "added",
                fields: [],
                before: null,
                after: { id: 1, order_id: 1042 },
              },
            ],
            skipped: [{ table: "audit", why: "no primary key" }],
            truncated: 2,
          },
        }}
      />,
    );
    expect(screen.getByText("Records before and after")).toBeInTheDocument();
    expect(
      screen.getByText("orders [1042] · Changed: status"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Before: id: 1042, status: paid"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("After: id: 1042, status: refunded"),
    ).toBeInTheDocument();
    expect(screen.getByText("refunds [1] · Added")).toBeInTheDocument();
    expect(
      screen.getByText("Not compared: audit (no primary key)"),
    ).toBeInTheDocument();
    expect(screen.getByText("And 2 more changes.")).toBeInTheDocument();
  });

  it("says no record changed when the trial changed none", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [],
          decisions: [],
          records: { changes: [], skipped: [], truncated: 0 },
        }}
      />,
    );
    expect(
      screen.getByText("No record changed in this trial."),
    ).toBeInTheDocument();
  });

  it("says how the trial started as the server words it, and nothing when the server has nothing to say", () => {
    const line =
      "Started from the same records as the first trial: compared with that trial's start, not a sealed seed";
    render(
      <TrialChangesPanel
        trialState={{
          start: { state: "seeded", tables: [], against: "first_trial", line },
        }}
      />,
    );
    expect(screen.getByText(line)).toBeInTheDocument();
    cleanup();
    render(
      <TrialChangesPanel
        trialState={{ start: { state: "differs", tables: ["orders"], line: null } }}
      />,
    );
    expect(screen.queryByText(/Started from/)).toBeNull();
  });
  it("puts a trial that did not start clean under Worth knowing, in the server's notice", () => {
    const message =
      "Did not start from the seeded records: orders differed. Results of this trial may not repeat.";
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [],
          decisions: [],
          start: { state: "differs", tables: ["orders"] },
          notices: [{ code: "start_differs", message }],
        }}
      />,
    );
    expect(
      screen.getByText("Worth knowing about this trial"),
    ).toBeInTheDocument();
    expect(screen.getAllByText(message)).toHaveLength(1);
  });

  it("says why no record changed when the agent committed outside the test world, in place of no record changed", () => {
    const message =
      "The agent committed changes, but no record of the test world changed: its tools keep their records in the agent's own process.";
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [
            {
              who: "agent",
              name: "the agent",
              attempted: 1,
              committed: 1,
              committed_actions: ["issue_refund"],
            },
          ],
          decisions: [],
          records: { changes: [], skipped: [], truncated: 0 },
          notices: [{ code: "changed_outside_world", message }],
        }}
      />,
    );
    expect(screen.getByTestId("changed-outside-world")).toHaveTextContent(
      message,
    );
    expect(screen.queryByText("No record changed in this trial.")).toBeNull();
    expect(screen.queryByText("Worth knowing about this trial")).toBeNull();
  });

  it("says what changed could not be read, never that nothing changed", () => {
    render(
      <TrialChangesPanel
        trialState={{ state_changes: [], decisions: [], unreadable: true }}
      />,
    );
    expect(
      screen.getByText(
        "What changed in the world could not be read for this trial. Checks that needed it read Unmeasured.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Nothing changed in the world in this trial."),
    ).toBeNull();
  });

  it("says a refused approval was refused, and that the agent committed after the refusal, never that it waited", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [],
          decisions: [
            {
              actor: "manager",
              name: "Priya Raman",
              play: "live",
              action: "approvals.put",
              outcome: "committed",
              verdict: "refused",
              committed_after_refusal: true,
              line: "Priya Raman · Decided · approvals.put · committed · Refused",
            },
          ],
          approvals: [
            {
              request: { action: "approvals.request" },
              decision: {
                by: "manager",
                name: "Priya Raman",
                action: "approvals.put",
              },
              before_decision: [],
              after_decision: [{ action: "apply_discount", outcome: "committed" }],
              acted_before_decision: false,
              verdict: "refused",
              committed_after_refusal: true,
            },
            // An older log kept neither the request's action nor a decision.
            {
              request: { action: null },
              decision: { by: null, name: null, action: null },
              before_decision: [],
              after_decision: [],
              acted_before_decision: false,
            },
          ],
        }}
      />,
    );
    expect(
      screen.getByText(
        "Priya Raman · Decided · approvals.put · committed · Refused",
      ),
    ).toBeInTheDocument();
    expect(screen.getByTestId("committed-after-refusal")).toHaveTextContent(
      "The agent committed the change after Priya Raman refused it",
    );
    expect(
      screen.getByText(
        "approvals.request, then Priya Raman: approvals.put · Refused · the agent committed the change anyway · then the agent wrote: apply_discount (committed)",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "A request, then no decision · the agent waited for the decision",
      ),
    ).toBeInTheDocument();
  });

  it("says an approved request was approved, with no line about acting after a refusal", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [],
          decisions: [
            {
              actor: "manager",
              name: "Priya Raman",
              play: "live",
              action: "approvals.put",
              outcome: "committed",
              verdict: "approved",
              committed_after_refusal: false,
              line: "Priya Raman · Decided · approvals.put · committed · Approved",
            },
          ],
        }}
      />,
    );
    expect(
      screen.getByText(
        "Priya Raman · Decided · approvals.put · committed · Approved",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("committed-after-refusal")).toBeNull();
  });

  it("reads whether the trial's records could be read from the one flag the server sets", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [],
          decisions: [],
          unreadable: false,
          records: { changes: [], skipped: [], truncated: 0 },
        }}
      />,
    );
    expect(screen.getByText("No record changed in this trial.")).toBeInTheDocument();
    expect(screen.queryByText(/could not be read/)).toBeNull();
    cleanup();
    // The unreadable flag wins over kept records.
    render(
      <TrialChangesPanel
        trialState={{
          unreadable: true,
          records: { changes: [], skipped: [], truncated: 0 },
        }}
      />,
    );
    expect(screen.getByText(/could not be read for this trial/)).toBeInTheDocument();
    expect(screen.queryByText("No record changed in this trial.")).toBeNull();
  });

  it("shows every notice the server sends in its own words, whatever its code", () => {
    const notices = [
      {
        code: "fault_not_applied",
        message: "The timeout on issue_refund was never applied.",
        action: "issue_refund",
        on_call: 1,
        mode: "timeout_before_commit",
      },
      {
        code: "fault_unknown_action",
        message: "A test condition names refunds.void, which this world has no action for.",
        action: "refunds.void",
        on_call: 1,
        mode: "unavailable",
      },
      {
        code: "fault_unreadable",
        message: "The test conditions of this trial could not be read.",
        action: null,
        on_call: null,
        mode: null,
      },
      {
        code: "duplicates_unreadable",
        message: "Whether a write was committed twice could not be read.",
        action: null,
        on_call: null,
        mode: null,
      },
      {
        code: "evidence_incomplete",
        message: "2 checks had no evidence and read Unmeasured.",
        count: 2,
      },
    ];
    render(<TrialChangesPanel trialState={{ notices }} />);
    expect(screen.getByText("Worth knowing about this trial")).toBeInTheDocument();
    for (const one of notices)
      expect(screen.getByText(one.message)).toBeInTheDocument();
  });
});

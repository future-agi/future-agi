import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import TrialChangesPanel from "../TrialChangesPanel";

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
              play: "live",
              action: "crm.request_approval",
              why: "12% is within my limit",
            },
            {
              actor: "manager",
              play: "live",
              action: null,
              why: "already approved",
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

  it("reads an actor's refused action apart from a committed one", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [
            {
              who: "coworker",
              name: "Sam Lee",
              attempted: 1,
              committed: 0,
              refused: 1,
              faulted: 0,
              duplicate: 0,
              committed_actions: [],
            },
            {
              who: "manager",
              name: "Priya Raman",
              attempted: 1,
              committed: 1,
              refused: 0,
              faulted: 0,
              duplicate: 0,
              committed_actions: ["crm.approve_discount"],
            },
          ],
          decisions: [
            {
              actor: "coworker",
              play: "live",
              action: "crm.approve_discount",
              outcome: "refused",
            },
            {
              actor: "manager",
              play: "mocked",
              action: "crm.approve_discount",
              outcome: "committed",
            },
          ],
        }}
      />,
    );
    expect(
      screen.getByText(
        "Sam Lee · Decided · crm.approve_discount · refused by the world",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Priya Raman · As scheduled · crm.approve_discount · committed",
      ),
    ).toBeInTheDocument();
  });

  it("says an actor whose trigger never came in plainly, never as a raw code", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [],
          decisions: [
            {
              actor: "dana",
              play: "not_triggered",
              why: "its trigger never came in this trial",
            },
          ],
        }}
      />,
    );
    expect(screen.getByText(/Never came in/)).toBeInTheDocument();
    expect(screen.queryByText(/not_triggered/)).not.toBeInTheDocument();
  });

  it("says nothing changed when the trial recorded nothing", () => {
    render(
      <TrialChangesPanel trialState={{ state_changes: [], decisions: [] }} />,
    );
    expect(
      screen.getByText("Nothing changed in the world in this trial."),
    ).toBeInTheDocument();
  });
  it("shows the world waiting on an actor and the configured outcome when it does not answer", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [
            {
              who: "deputy",
              name: "Dev",
              attempted: 1,
              committed: 1,
              committed_actions: ["deals.approve"],
            },
          ],
          decisions: [
            {
              actor: "manager",
              play: "waiting",
              timeout_seconds: 30,
              fallback: "decline",
            },
            {
              actor: "manager",
              play: "timeout",
              fallback: "decline",
              outcome: "declined",
            },
            {
              actor: "lead",
              play: "waiting",
              timeout_seconds: 20,
              fallback: "deputy",
            },
            {
              actor: "lead",
              play: "timeout",
              fallback: "deputy",
              outcome: "answered_by",
            },
          ],
        }}
      />,
    );
    expect(
      screen.getByText(
        "manager · Waiting for a response · up to 30 s, then the request is declined",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "manager · Timed out · No answer: the request was declined",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "lead · Waiting for a response · up to 20 s, then Dev answers instead",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("lead · Timed out · No answer: Dev answered instead"),
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
              play: "timeout",
              fallback: "approvals.change",
              outcome: "fallback_action",
              action_outcome: "refused",
            },
            {
              actor: "alex",
              play: "timeout",
              fallback: "approvals.change",
              outcome: "fallback_action",
              action_outcome: "committed",
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

  it("names an actor that could not take part as the scenario's problem, and an open wait", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [],
          decisions: [
            { actor: "auditor", play: "problem", problem: "no_world_door" },
            { actor: "manager", play: "timeout", outcome: "unresolved_at_end" },
          ],
        }}
      />,
    );
    expect(
      screen.getByText(
        "auditor · Could not take part · Could not act: this agent's tools run where no actor can act",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "manager · Timed out · Still waited on when the trial ended",
      ),
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
          decisions: [
            {
              actor: "priya",
              play: "history",
              action: "slack.post",
              arguments: { text: "20% for Acme?" },
            },
          ],
          approvals: [
            {
              request: { action: "request_discount" },
              decision: { by: "priya", action: "approve" },
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

  it("names a pre-run writer who changed nothing in the trial by the name its message carries", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [],
          decisions: [
            {
              actor: "lead",
              name: "Sam Lee",
              play: "history",
              action: "chat.post_message",
              arguments: { text: "Margins are tight on deal 9." },
            },
          ],
        }}
      />,
    );
    expect(
      screen.getByText("Sam Lee: Margins are tight on deal 9."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^lead:/)).toBeNull();
  });

  it("says each actor's model calls, tokens and cost, then the trial's in all", () => {
    const call = (actor, model, cost) => ({
      actor,
      model,
      tokens_in: 10,
      tokens_out: 5,
      cost_usd: cost,
    });
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
          usage: [
            call("priya", "m1", 0.001),
            call("priya", "m1", 0.002),
            call("ravi", "", 0.0005),
          ],
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

  it("shows a worker the world told apart, with each attempt it refused and why", () => {
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: [
            {
              who: "worker:order_lookup",
              name: "order_lookup",
              attempted: 2,
              committed: 1,
              refused: 1,
              faulted: 0,
              duplicate: 0,
              committed_actions: ["get_order"],
              refused_actions: [
                {
                  action: "update_order_status",
                  code: "not_permitted",
                  reason:
                    "orders.update_status is not permitted for order_lookup",
                },
              ],
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("Worker · order_lookup")).toBeInTheDocument();
    expect(screen.getByTestId("refused-attempts")).toHaveTextContent(
      "update_order_status: orders.update_status is not permitted for order_lookup",
    );
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

  const twoWorkers = [
    {
      who: "worker:order_lookup",
      name: "order_lookup",
      attempted: 1,
      committed: 1,
      committed_actions: ["list_refunds"],
    },
    {
      who: "worker:refund",
      name: "refund",
      attempted: 2,
      committed: 2,
      committed_actions: ["issue_refund"],
    },
  ];
  const record = {
    key: "order_id=1042",
    argument: "order_id",
    value: "1042",
    actions: [
      {
        who: "worker:order_lookup",
        tool: "list_refunds",
        outcome: "committed",
        write: null,
      },
      {
        who: "worker:refund",
        tool: "issue_refund",
        outcome: "committed",
        write: true,
      },
    ],
    committed_writes: 1,
    truncated: false,
  };

  it("lists a record two participants named, with each action and the committed writes", () => {
    render(
      <TrialChangesPanel
        trialState={{ state_changes: twoWorkers, shared_records: [record] }}
      />,
    );
    const section = screen.getByTestId("shared-records");
    expect(section.textContent).toContain(
      "Records touched by more than one participant",
    );
    expect(section.textContent).toContain("order_id 1042");
    expect(section.textContent).toContain(
      "refund · issue_refund · committed (write)",
    );
    expect(section.textContent).toContain(
      "order_lookup · list_refunds · committed",
    );
    expect(section.textContent).toContain("Committed writes: 1");
    expect(section.textContent).not.toContain("More records not shown");
  });

  it("hides the section when no record was shared and says when records were cut", () => {
    const { unmount } = render(
      <TrialChangesPanel
        trialState={{ state_changes: twoWorkers, shared_records: [] }}
      />,
    );
    expect(screen.queryByTestId("shared-records")).toBeNull();
    unmount();
    render(
      <TrialChangesPanel
        trialState={{
          state_changes: twoWorkers,
          shared_records: [{ ...record, truncated: true }],
        }}
      />,
    );
    expect(
      screen.getByText(
        "More records not shown: this trial's record reached its size limit",
      ),
    ).toBeInTheDocument();
  });
});

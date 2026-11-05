import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import TrialChangesPanel from "../TrialChangesPanel";

describe("TrialChangesPanel", () => {
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

  it("says nothing changed when the trial recorded nothing", () => {
    render(
      <TrialChangesPanel trialState={{ state_changes: [], decisions: [] }} />,
    );
    expect(
      screen.getByText("Nothing changed in the world in this trial."),
    ).toBeInTheDocument();
  });
});

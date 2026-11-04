import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { functionCallTranscriptRows } from "src/api/simulate-environments/runDetail";
import TranscriptView from "../TranscriptView";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/sections/common/CellMarkdown", () => ({
  default: ({ text }) => <div>{text}</div>,
}));

const customer = {
  id: "1",
  speaker_role: "user",
  content: "Can I get a discount?",
  start_time_ms: 1000,
};

const showActions = (actions) =>
  render(
    <TranscriptView
      customerName="Maya"
      transcript={[customer, ...functionCallTranscriptRows(actions)]}
    />,
  );

describe("world actors in voice transcripts", () => {
  it("shows a world actor's action as an event, with its raw record folded behind Details", async () => {
    showActions([
      {
        id: "approve-1",
        name: "crm.approve_discount",
        arguments: { percent: 20 },
        result: { approved: false },
        actor_id: "manager",
        actor_name: "Arjun Mehta",
        actor_role: "store manager",
        outcome: "refused",
        start_time_ms: 2000,
      },
    ]);

    expect(screen.getAllByText("Arjun Mehta · store manager")).toHaveLength(2);
    expect(
      screen.getByText("Refused · crm.approve_discount"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Function call · crm.approve_discount/),
    ).toBeNull();
    expect(screen.queryByText(/args:/)).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: /^Details/ }));
    expect(screen.getByText(/→ args: .*"percent":20/)).toBeInTheDocument();
    expect(
      screen.getByText(/← result: .*"approved":false/),
    ).toBeInTheDocument();
  });

  it("names how each action went: committed, refused or failed", () => {
    showActions(
      [
        ["committed", "crm.note"],
        ["refused", "crm.refund"],
        ["faulted", "crm.cancel"],
      ].map(([outcome, name], i) => ({
        id: `a-${i}`,
        name,
        outcome,
        error: outcome === "faulted" ? "timeout" : undefined,
        actor_id: "manager",
        actor_name: "Arjun Mehta",
        start_time_ms: 2000 + i,
      })),
    );
    expect(screen.getByText("Committed · crm.note")).toBeInTheDocument();
    expect(screen.getByText("Refused · crm.refund")).toBeInTheDocument();
    expect(screen.getByText("Failed · crm.cancel")).toBeInTheDocument();
  });

  it("shows just the name and the action for a row stored without a role or an outcome", () => {
    showActions([
      {
        id: "a",
        name: "crm.lookup",
        actor_id: "manager",
        actor_name: "Arjun Mehta",
        start_time_ms: 2000,
      },
      {
        id: "b",
        name: "crm.hold",
        actor_id: "clerk",
        actor_name: "",
        start_time_ms: 3000,
      },
    ]);
    expect(screen.getAllByText("Arjun Mehta")).toHaveLength(2);
    expect(screen.getAllByText("clerk")).toHaveLength(2);
    expect(screen.getByText("crm.lookup")).toBeInTheDocument();
    expect(screen.queryByText(/undefined|^Action| ·$/)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Details/ })).toBeNull();
  });

  it("lists world actors among who was on the call and under Others", async () => {
    showActions([
      {
        id: "a",
        name: "crm.lookup",
        actor_id: "manager",
        actor_name: "Arjun Mehta",
        actor_role: "store manager",
        outcome: "committed",
        start_time_ms: 2000,
      },
    ]);
    expect(screen.getByText("On the call:")).toBeInTheDocument();
    expect(screen.getByText("Maya · Customer")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Others" }));
    expect(screen.getByText("Committed · crm.lookup")).toBeInTheDocument();
    expect(screen.queryByText("Can I get a discount?")).toBeNull();
  });
});

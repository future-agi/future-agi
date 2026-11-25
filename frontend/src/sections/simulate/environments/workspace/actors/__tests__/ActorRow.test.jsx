import { describe, it, expect, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import ActorRow from "../ActorRow";
import { typedActor } from "./castFixtures";

vi.mock("src/api/simulate-environments/versions", () => ({
  useActorView: () => ({}),
}));

const specCard = (title) => screen.getByText(title).parentElement;
const specValue = (card, label) =>
  within(card).getByText(label).nextElementSibling.textContent;

describe("ActorRow", () => {
  it("says in how many scenarios the actor comes in its own way, and how, as the server lists them", async () => {
    const sam = {
      ...typedActor({
        id: "sam",
        name: "Sam",
        channel: "chat_message",
        trigger: { kind: "mid_journey", turn: 1 },
      }),
      when_label: "After the agent's first turn",
      arrival_overrides: [
        {
          scenario: "Refund on day two",
          when_label:
            "After the agent's turn 2, or before the person closes the chat, if sooner",
        },
      ],
    };
    render(<ActorRow envId="env-1" actor={sam} loudChannels={[]} />);

    const line = screen.getByText("Comes in differently in 1 scenario");
    fireEvent.mouseOver(line);
    expect(
      await screen.findByText(
        /Refund on day two: After the agent's turn 2, or before the person closes the chat/,
      ),
    ).toBeInTheDocument();
  });

  it("says nothing more when no scenario brings the actor in its own way", () => {
    render(
      <ActorRow
        envId="env-1"
        actor={typedActor({ id: "sam", name: "Sam" })}
        loudChannels={[]}
      />,
    );
    expect(screen.queryByText(/Comes in differently/)).toBeNull();
  });

  it("shows the goal as its line, and on the right when a world actor comes in and how many use it", () => {
    render(
      <ActorRow
        envId="env-1"
        actor={typedActor({
          id: "sam",
          name: "Sam",
          role: "approver",
          goal: "Approve fast",
          scenarios: 3,
          when_label: "When contacted",
        })}
        loudChannels={[]}
      />,
    );
    expect(screen.getByText("Sam")).toBeInTheDocument();
    expect(screen.getByText("· approver")).toBeInTheDocument();
    expect(screen.getByText("Approve fast")).toBeInTheDocument();
    expect(screen.getByText("When contacted · Used by 3")).toBeInTheDocument();
  });

  it("counts the scenarios a caller-side actor is in on the right", () => {
    render(
      <ActorRow
        envId="env-1"
        actor={typedActor({
          id: "sam",
          name: "Sam",
          side: "caller",
          scenarios: 1,
        })}
        loudChannels={[]}
      />,
    );
    expect(screen.getByText("From the start · 1 scenario")).toBeInTheDocument();
  });

  it("shows the caller's line in place of its goal, and only when it comes in on the right", () => {
    render(
      <ActorRow
        envId="env-1"
        actor={typedActor({
          id: "caller",
          name: "Dana",
          side: "caller",
          goal: "Get a refund",
          scenarios: 2,
        })}
        callerLine="Has the scenario's task · in 2 scenarios"
        loudChannels={[]}
      />,
    );
    expect(
      screen.getByText("Has the scenario's task · in 2 scenarios"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Get a refund")).toBeNull();
    expect(screen.getByText("From the start")).toBeInTheDocument();
    expect(screen.queryByText(/· 2 scenarios$/)).toBeNull();
  });

  it("opens the details on a click of the row, starting with how the actor takes part, and closes them again", async () => {
    render(
      <ActorRow
        envId="env-1"
        actor={typedActor({
          id: "sam",
          name: "Sam",
          channel: "audio_mix",
          permitted_actions: ["crm.approve_discount"],
          scenarios: 2,
        })}
        loudChannels={["audio_mix"]}
      />,
    );
    expect(screen.getByText("From the start · Used by 2")).toBeInTheDocument();
    expect(screen.queryByText("Their part")).toBeNull();
    fireEvent.click(screen.getByText("Sam"));
    const part = specCard("Their part");
    expect(specValue(part, "Actions they may take")).toBe(
      "crm.approve_discount",
    );
    expect(specValue(part, "When they come in")).toBe("From the start");
    expect(specValue(part, "Voice")).toBe("Loudness automatic");
    expect(specValue(part, "If they don't answer")).toBe("Nothing happens");
    expect(within(part).queryByText("How they join")).toBeNull();
    fireEvent.click(screen.getByText("Sam"));
    await waitFor(() => expect(screen.queryByText("Their part")).toBeNull());
  });
});

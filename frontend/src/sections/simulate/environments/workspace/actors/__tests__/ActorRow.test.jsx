import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import ActorRow from "../ActorRow";
import { typedActor } from "./castFixtures";

vi.mock("src/api/simulate-environments/versions", () => ({
  useActorView: () => ({}),
}));

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
});

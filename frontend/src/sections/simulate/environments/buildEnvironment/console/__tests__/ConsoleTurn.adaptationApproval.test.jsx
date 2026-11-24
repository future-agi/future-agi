import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";

import { Turn } from "../ConsoleTurn";

const builder = (steps) => ({ id: "t1", role: "builder", steps });
const approval = {
  prompt: "Use this binding for ShopMate on retail?\n\nMapped tools (1):",
  options: [{ label: "yes" }, { label: "no" }],
  multiSelect: false,
  step: 1,
  total: 1,
};

describe("an adaptation's approval in the chat", () => {
  it("points to Review the design instead of asking here", () => {
    const { rerender } = render(
      <Turn turn={builder([{ id: "a1", kind: "ask", question: approval }])} />,
    );

    expect(
      screen.getByText("Waiting for your approval in Review the design"),
    ).toBeInTheDocument();
    expect(screen.queryByText("yes")).toBeNull();

    rerender(
      <Turn
        turn={builder([
          {
            id: "a1",
            kind: "ask",
            question: approval,
            resolved: true,
            answerText: "yes",
          },
        ])}
      />,
    );
    expect(
      screen.getByText("Approved in Review the design"),
    ).toBeInTheDocument();
  });

  it.each([
    [
      { answerText: "Map get_order to order_details instead" },
      "You replied: “Map get_order to order_details instead”",
    ],
    [{ answerText: "no" }, "You replied: “no”"],
    [
      {
        answerText: "yes",
        answerFailed: { reason: "The builder has finished." },
      },
      "Your reply wasn't taken: “yes”. The builder has finished.",
    ],
  ])("shows any other answer as it was given", (answer, text) => {
    render(
      <Turn
        turn={builder([
          {
            id: "a1",
            kind: "ask",
            question: approval,
            resolved: true,
            ...answer,
          },
        ])}
      />,
    );
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.queryByText("Approved in Review the design")).toBeNull();
  });
});

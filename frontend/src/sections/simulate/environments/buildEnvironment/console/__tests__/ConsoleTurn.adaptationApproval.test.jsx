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
          { id: "a1", kind: "ask", question: approval, resolved: true },
        ])}
      />,
    );
    expect(
      screen.getByText("Approved in Review the design"),
    ).toBeInTheDocument();
  });
});

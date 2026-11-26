import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import DesignWorkspace from "../DesignWorkspace";
import { designQuestion } from "../designQuestion";

vi.mock("../../components/ChatSplitPane", () => ({
  default: ({ children }) => children,
}));

const question = {
  message_id: "approval-1",
  design_proposal: {
    one_liner: "Returns desk",
    summary: "A customer requests a refund.",
    records: ["Orders"],
    tools: ["Refund lookup"],
    rules: ["Refunds within 30 days"],
    people: ["Supervisor"],
    use_cases: ["Approve an eligible return"],
  },
};

describe("Design approval", () => {
  it("uses the proposed items and requests a new approval after an omission", () => {
    const send = vi.fn();
    const { rerender } = render(
      <DesignWorkspace
        canEdit
        chat={{ send }}
        question={{
          ...question,
          design_proposal: {
            ...question.design_proposal,
            dependencies: ["External identity check"],
          },
        }}
      />,
    );
    expect(screen.getByText("Review the design")).toBeInTheDocument();
    expect(screen.getByText("Orders")).toBeInTheDocument();
    expect(screen.getByText("Supervisor")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Approve and build" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Leave out" }));
    expect(send).toHaveBeenCalledWith(
      expect.stringContaining(
        "Leave out this dependency: External identity check",
      ),
    );
    rerender(<DesignWorkspace canEdit chat={{ send }} question={question} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve and build" }));
    expect(send).toHaveBeenLastCalledWith("Build this");
  });

  it("keeps legacy approval questions and suppresses already-answered or regular questions", () => {
    const legacy = {
      message_id: "one",
      prompt: "Approve the returns desk?",
      options: ["Build this: Approve", "Change something: Request a change"],
    };
    expect(designQuestion({ blocking_input: legacy })).toBe(legacy);
    expect(
      designQuestion({
        blocking_input: legacy,
        messages: [{ reply_to: "one" }],
      }),
    ).toBeNull();
    expect(
      designQuestion({ blocking_input: { options: ["Chat", "Voice"] } }),
    ).toBeNull();
  });
});

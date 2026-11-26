import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import DesignWorkspace from "../DesignWorkspace";
import { designQuestion } from "../designQuestion";

vi.mock("../../components/ChatSplitPane", () => ({
  default: ({ children }) => children,
}));

const sourceReview = vi.hoisted(() => vi.fn(() => ({ data: null })));
vi.mock("src/api/simulate-environments/sourceImports", () => ({
  useEnvironmentSourceReview: sourceReview,
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

  it("shows real file names and only attributes clauses backed by a visible source", () => {
    sourceReview.mockReturnValueOnce({
      data: {
        state: "accepted",
        preview: {
          sources: [
            {
              filename: "Returns.pdf",
              pin: { source_id: "src_p", role: "policy" },
              visibility: "agent_visible",
            },
            {
              filename: "Grader.pdf",
              pin: { source_id: "src_g", role: "evaluation_reference" },
              visibility: "evaluator_only",
            },
          ],
        },
      },
    });
    render(
      <DesignWorkspace
        chat={{ send: vi.fn() }}
        sourceReviewId="review-1"
        question={{
          ...question,
          design_proposal: {
            ...question.design_proposal,
            rule_sources: [
              { rule_index: 0, source_id: "src_p", page: 2 },
              { rule_index: 0, source_id: "src_g", page: 1 },
            ],
          },
        }}
      />,
    );
    expect(screen.getByText("From Returns.pdf · page 2")).toBeVisible();
    expect(
      screen.queryByText("From Grader.pdf · page 1"),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", {
        name: "What the builder took from Returns.pdf",
      }),
    );
    expect(screen.getByText("Reviewed source")).toBeVisible();
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

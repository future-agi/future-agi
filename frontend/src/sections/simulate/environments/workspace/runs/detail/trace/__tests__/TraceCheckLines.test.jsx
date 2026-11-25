import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TraceCheckLines from "../TraceCheckLines";
import StatusFilterChips from "../StatusFilterChips";

const goals = (verdicts) =>
  verdicts.map((passed, index) => ({ name: `goal_${index + 1}`, passed }));

const evals = (verdicts) =>
  verdicts.map((passed, index) => ({
    id: `eval-${index + 1}`,
    name: `eval_${index + 1}`,
    passed,
    status: passed == null ? "pending" : "succeeded",
  }));

describe("TraceCheckLines", () => {
  it("puts the group with a failure first and its failures at the top", () => {
    render(
      <TraceCheckLines
        subGoals={goals([true, true])}
        requiredEvals={evals([true, false])}
      />,
    );

    const groups = screen.getAllByTestId(/^trace-check-group-/);
    expect(groups.map((group) => group.dataset.testid)).toEqual([
      "trace-check-group-evals",
      "trace-check-group-subGoals",
    ]);
    expect(groups[0]).toHaveTextContent("Required evals · 1 of 2 failed");
    expect(within(groups[0]).getAllByText(/^eval_/)[0]).toHaveTextContent(
      "eval_2",
    );
    expect(groups[1]).toHaveTextContent("Sub-goals · all 2 met");
  });

  it("counts only the sub-goals that apply", () => {
    render(
      <TraceCheckLines
        subGoals={[
          ...goals([true, true]),
          { name: "loyalty", passed: null, result: "not_applicable" },
          { name: "confirmation", passed: null, result: "not_applicable" },
        ]}
      />,
    );
    expect(screen.getByTestId("trace-check-group-subGoals")).toHaveTextContent(
      "Sub-goals · all 2 that apply met",
    );
  });

  it("says a call nothing was scored on was not evaluated, not 0 met", () => {
    render(
      <TraceCheckLines notEvaluated subGoals={goals([null, null, null])} />,
    );
    expect(screen.getByTestId("trace-check-group-subGoals")).toHaveTextContent(
      "Sub-goals · not evaluated",
    );
  });

  it("keeps a verdict the harness gave even when the call was not scored", () => {
    render(<TraceCheckLines notEvaluated subGoals={goals([true, null])} />);
    expect(screen.getByTestId("trace-check-group-subGoals")).toHaveTextContent(
      "Sub-goals · 1 of 2 met",
    );
  });

  it("folds a long group and opens it in place", async () => {
    render(
      <TraceCheckLines subGoals={goals([false, true, true, true, true])} />,
    );

    expect(screen.getAllByText(/^goal_/)).toHaveLength(3);
    await userEvent.click(screen.getByRole("button", { name: "+ 2 more" }));
    expect(screen.getAllByText(/^goal_/)).toHaveLength(5);
  });

  it("counts the required evals that couldn't score", () => {
    render(
      <TraceCheckLines
        requiredEvals={[
          { id: "e1", name: "gating", passed: null, status: "failed" },
          { id: "e2", name: "tone", passed: null, status: "failed" },
        ]}
      />,
    );

    expect(screen.getByTestId("trace-check-group-evals")).toHaveTextContent(
      "Required evals · 0 of 2 passed · 2 couldn't score",
    );
  });

  it("shows only the count while every check is still waiting", () => {
    render(<TraceCheckLines requiredEvals={evals([null, null, null])} />);

    expect(screen.getByTestId("trace-check-group-evals")).toHaveTextContent(
      "Required evals · 3 waiting",
    );
    expect(screen.queryByText(/^eval_/)).not.toBeInTheDocument();
  });
});

describe("StatusFilterChips", () => {
  it("hides a status with no calls, but keeps All and the selected one", () => {
    render(
      <StatusFilterChips
        value="passed"
        counts={{ all: 3, error: 3, passed: 0, failed: 0 }}
        onChange={() => {}}
      />,
    );

    expect(screen.getByRole("button", { name: "All" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Error" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Passed" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Failed" }),
    ).not.toBeInTheDocument();
  });
});

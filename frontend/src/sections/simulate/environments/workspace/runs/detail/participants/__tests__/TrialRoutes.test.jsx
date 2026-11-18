import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

const asked = [];
let routes;
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useTrialRoutes: (...args) => {
    asked.push(args);
    return { data: routes };
  },
}));

const { default: TrialRoutes } = await import("../TrialRoutes");

const column = (to) => ({
  key: `handoff:coordinator:>worker:${to}`,
  kind: "handoff",
  from_kind: "coordinator",
  from_id: null,
  from_name: "Returns coordinator",
  to_kind: "worker",
  to_id: to,
  to_name: `${to} worker`,
  tool: null,
});
const cell = (state, step) => ({
  state,
  step,
  steps: step == null ? [] : [step],
  count: 1,
});
const trial = (number, result, cells, route = "recorded") => ({
  number,
  call_execution_id: `c${number}`,
  result,
  route,
  cells,
});
// Trials 1 and 3 asked the refund worker; trial 2's refund worker never answered.
const SPLIT = {
  scenario_key: "refund",
  tool_calls: false,
  columns: [column("lookup"), column("refund")],
  trials: [
    trial(1, "passed", [cell("step", 1), cell("step", 5)]),
    trial(2, "failed", [cell("step", 1), cell("no_answer", 5)]),
    trial(3, "passed", [cell("step", 1), cell("step", 4)]),
  ],
  split: {
    column: 1,
    key: "handoff:coordinator:>worker:refund",
    groups: [
      { state: "step", count: 1, trials: [1, 3] },
      { state: "no_answer", count: 1, trials: [2] },
    ],
  },
  same_route: false,
};

const draw = (props = {}) => {
  const onOpen = vi.fn();
  render(
    <TrialRoutes
      testExecutionId="ex1"
      scenarioKey="refund"
      currentId="c1"
      onOpen={onOpen}
      {...props}
    />,
  );
  return onOpen;
};

describe("TrialRoutes", () => {
  beforeEach(() => {
    asked.length = 0;
    routes = SPLIT;
  });

  it("says where the trials first went differently and how each went", () => {
    draw();
    expect(screen.getByText(/^3 trials$/)).toBeInTheDocument();
    expect(screen.getByTestId("trial-routes-split")).toHaveTextContent(
      "Split at Returns coordinator → refund worker: Asked in 1, 3 · No answer in 2",
    );
  });

  it("says every trial took the same route when none differ", () => {
    routes = { ...SPLIT, split: null, same_route: true };
    draw();
    expect(screen.getByTestId("trial-routes-split")).toHaveTextContent(
      "Every trial took the same route",
    );
  });

  it("shows nothing for a scenario with fewer than two trials", () => {
    routes = { ...SPLIT, trials: SPLIT.trials.slice(0, 1) };
    draw();
    expect(screen.queryByTestId("trial-routes-split")).toBeNull();
  });

  it("opens a trial at the step a cell names", () => {
    const onOpen = draw();
    fireEvent.click(screen.getByRole("button", { name: "Show trials" }));
    // Trial 2's cells: asked the lookup worker at step 1, no answer from the refund worker at step 5.
    const [, noAnswer] = screen.getAllByRole("button", {
      name: "Open trial 2 at this step",
    });
    fireEvent.click(noAnswer);
    expect(onOpen).toHaveBeenCalledWith("c2", 5);
  });

  it("names a cut-short column in full on hover", async () => {
    draw();
    fireEvent.click(screen.getByRole("button", { name: "Show trials" }));
    const header = screen
      .getAllByText("Returns coordinator → refund worker")
      .find((one) => one.closest("th"));
    fireEvent.mouseOver(header);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Returns coordinator → refund worker",
    );
  });

  it("lists a trial it did not compare, with the reason", () => {
    routes = {
      ...SPLIT,
      trials: [...SPLIT.trials, trial(4, "failed", null, "not_finished")],
    };
    draw();
    fireEvent.click(screen.getByRole("button", { name: "Show trials" }));
    expect(
      screen.getByText("Not compared: the trial didn't finish"),
    ).toBeInTheDocument();
  });

  it("asks for tool-call columns only when they are switched on", () => {
    draw();
    expect(asked.at(-1)).toEqual(["ex1", "refund", false]);
    fireEvent.click(screen.getByRole("button", { name: "Show trials" }));
    fireEvent.click(screen.getByRole("button", { name: "Show tool calls" }));
    expect(asked.at(-1)).toEqual(["ex1", "refund", true]);
  });
});

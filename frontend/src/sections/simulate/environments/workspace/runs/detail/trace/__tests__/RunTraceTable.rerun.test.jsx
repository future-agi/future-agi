import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

const useRunCalls = vi.fn();
vi.mock("src/api/simulate-environments/runDetail", () => ({
  useRunCalls: (...args) => useRunCalls(...args),
}));
const listMatchingCalls = vi.fn();
vi.mock("src/api/simulate-environments/rerunScenarios", async (orig) => ({
  ...(await orig()),
  listMatchingCalls: (...args) => listMatchingCalls(...args),
}));

const { default: RunTraceTable } = await import("../RunTraceTable");

// Two scenarios run three times each, plus one call the harness never tagged.
const task = (id, key, trial, status = "passed") => ({
  id,
  scenario: key ? `${key} · Trial ${trial}` : "Untagged call",
  goal: "Refunds",
  subGoalResults: [],
  persona: "Caller",
  personaDetails: null,
  status,
  critical: false,
  csat: null,
  turns: null,
  latencyMs: null,
  tokens: null,
  durationMs: null,
  evalResults: [],
  sourceScenarioKey: key,
  trialIndex: trial,
});
const TASKS = [
  task("r1", "refund", 1),
  task("r2", "refund", 2, "failed"),
  task("r3", "refund", 3),
  task("e1", "escalate", 1, "failed"),
  task("e2", "escalate", 2),
  task("e3", "escalate", 3),
  task("u1", null, null),
];

let total = TASKS.length;
beforeEach(() => {
  total = TASKS.length;
  listMatchingCalls.mockReset();
  useRunCalls.mockImplementation((_id, opts = {}) => {
    const status = opts.filters?.status?.[0];
    const tasks = TASKS.filter((t) => !status || t.status === status);
    return {
      tasks,
      columns: [],
      groups: [
        {
          label: "Refunds",
          rows: tasks,
          count: tasks.length,
          measured: tasks.length,
          passed: 0,
          agg: { evals: {} },
        },
      ],
      facets: {
        status: [
          { value: "passed", count: 4 },
          { value: "failed", count: 2 },
        ],
      },
      count: status ? tasks.length : total,
      totalPages: !status && total > tasks.length ? 3 : 1,
      isLoading: false,
    };
  });
});

const renderTable = (props = {}) => {
  const onOpenCall = vi.fn();
  const onRerunScenarios = vi.fn();
  const utils = render(
    <RunTraceTable
      executionId="ex1"
      onOpenCall={onOpenCall}
      onRerunScenarios={onRerunScenarios}
      runTrials={3}
      {...props}
    />,
  );
  return { ...utils, onOpenCall, onRerunScenarios };
};

// The table opens grouped and folded; open the group so the rows show.
const openRows = async (user) =>
  user.click(screen.getByRole("checkbox", { name: /Expand all/ }));
const rowBox = (name) =>
  screen.getByRole("checkbox", { name: `Select ${name}` });
const pageBox = () =>
  screen.getByRole("checkbox", { name: "Select all calls on this page" });

describe("RunTraceTable — selecting calls to re-run", () => {
  it("ticks a call without opening it", async () => {
    const user = userEvent.setup();
    const { onOpenCall } = renderTable();
    await openRows(user);

    await user.click(rowBox("refund · Trial 1"));

    expect(rowBox("refund · Trial 1")).toBeChecked();
    expect(onOpenCall).not.toHaveBeenCalled();
  });

  it("opens a call on row click and leaves its tick alone", async () => {
    const user = userEvent.setup();
    const { onOpenCall } = renderTable();
    await openRows(user);
    await user.click(rowBox("refund · Trial 1"));

    // The name shows in the details and Scenario columns; either opens it.
    await user.click(screen.getAllByText("refund · Trial 1")[0]);

    expect(onOpenCall).toHaveBeenCalledWith(
      expect.objectContaining({ id: "r1" }),
    );
    expect(rowBox("refund · Trial 1")).toBeChecked();
  });

  it("never ticks a trial's siblings for the user", async () => {
    const user = userEvent.setup();
    renderTable();
    await openRows(user);

    await user.click(rowBox("refund · Trial 1"));

    expect(rowBox("refund · Trial 2")).not.toBeChecked();
    expect(rowBox("refund · Trial 3")).not.toBeChecked();
  });

  it("gives a call without a scenario no checkbox", async () => {
    const user = userEvent.setup();
    renderTable();
    await openRows(user);

    expect(screen.getAllByText("Untagged call").length).toBeGreaterThan(0);
    expect(
      screen.queryByRole("checkbox", { name: "Select Untagged call" }),
    ).not.toBeInTheDocument();
  });

  it("counts calls and the scenarios behind them", async () => {
    const user = userEvent.setup();
    renderTable();
    await openRows(user);

    await user.click(rowBox("refund · Trial 1"));
    await user.click(rowBox("refund · Trial 2"));
    await user.click(rowBox("escalate · Trial 1"));

    expect(
      screen.getByText("3 calls selected · 2 scenarios"),
    ).toBeInTheDocument();
  });

  it("selects only this page from the header checkbox", async () => {
    const user = userEvent.setup();
    total = 120;
    renderTable();
    await openRows(user);

    await user.click(pageBox());

    expect(rowBox("escalate · Trial 3")).toBeChecked();
    expect(
      screen.getByText("6 calls selected · 2 scenarios"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("All 6 calls on this page are selected."),
    ).toBeInTheDocument();
  });

  it("selects every matching call from the banner, and leaves out the ones unticked after", async () => {
    const user = userEvent.setup();
    total = 120;
    renderTable();
    await openRows(user);
    await user.click(pageBox());

    await user.click(
      screen.getByRole("button", { name: "Select all 120 matching calls" }),
    );
    expect(
      screen.getByText("All 120 matching calls are selected."),
    ).toBeInTheDocument();

    await user.click(rowBox("refund · Trial 2"));
    expect(rowBox("refund · Trial 2")).not.toBeChecked();
    expect(
      screen.getByText(
        "All 120 matching calls are selected except 1 you unticked.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("119 calls selected")).toBeInTheDocument();
  });

  it("offers every matching call only when another page holds more", async () => {
    // One page: the untagged call makes the count larger than the page's
    // selectable calls, but there is nothing more to select.
    const user = userEvent.setup();
    renderTable();
    await openRows(user);

    await user.click(pageBox());

    expect(
      screen.getByText("6 calls selected · 2 scenarios"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/matching calls/)).not.toBeInTheDocument();
  });

  it("clears the selection when the status filter changes", async () => {
    const user = userEvent.setup();
    renderTable();
    await openRows(user);
    await user.click(rowBox("refund · Trial 1"));

    await user.click(screen.getByRole("button", { name: /Failed/ }));

    expect(screen.queryByText(/selected/)).not.toBeInTheDocument();
  });
});

describe("RunTraceTable — the Re-run menu", () => {
  const openMenu = async (user) =>
    user.click(screen.getByRole("button", { name: /^Re-run/ }));

  it("re-runs each selected scenario once, at this run's trials", async () => {
    const user = userEvent.setup();
    const { onRerunScenarios } = renderTable();
    await openRows(user);
    await user.click(rowBox("refund · Trial 1"));
    await user.click(rowBox("refund · Trial 3"));
    await user.click(rowBox("escalate · Trial 2"));

    await openMenu(user);
    expect(screen.getByText("Repeats: 3")).toBeInTheDocument();
    expect(
      screen.getByText("2 scenarios × 3 repeats = 6 calls"),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("menuitem", { name: /Run as a new simulation/ }),
    );
    expect(onRerunScenarios).toHaveBeenCalledWith(["refund", "escalate"], 3);
  });

  it("keeps the selection after starting, so a refused start can be tried again", async () => {
    const user = userEvent.setup();
    const { onRerunScenarios } = renderTable();
    await openRows(user);
    await user.click(rowBox("refund · Trial 1"));
    await openMenu(user);

    await user.click(
      screen.getByRole("menuitem", { name: /Run as a new simulation/ }),
    );

    expect(onRerunScenarios).toHaveBeenCalledTimes(1);
    expect(rowBox("refund · Trial 1")).toBeChecked();
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("shows the latest scenario count when the menu is reopened mid-count", async () => {
    const user = userEvent.setup();
    total = 120;
    let finishFirst;
    listMatchingCalls
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishFirst = resolve;
          }),
      )
      .mockResolvedValueOnce({
        callIds: ["r1", "e1"],
        scenarioKeys: ["refund", "escalate"],
      });
    renderTable();
    await openRows(user);
    await user.click(pageBox());
    await user.click(
      screen.getByRole("button", { name: "Select all 120 matching calls" }),
    );

    await openMenu(user);
    expect(screen.getByText("Counting scenarios…")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await openMenu(user);
    const latest = "2 scenarios × 3 repeats = 6 calls";
    expect(await screen.findByText(latest)).toBeInTheDocument();

    finishFirst({
      callIds: ["r1", "e1", "t1"],
      scenarioKeys: ["refund", "escalate", "timeout"],
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByText(latest)).toBeInTheDocument();
  });

  it("offers to try again when the selected calls can't be read", async () => {
    const user = userEvent.setup();
    total = 120;
    listMatchingCalls
      .mockRejectedValueOnce(new Error("Network Error"))
      .mockResolvedValueOnce({
        callIds: ["r1", "e1"],
        scenarioKeys: ["refund", "escalate"],
      });
    renderTable();
    await openRows(user);
    await user.click(pageBox());
    await user.click(
      screen.getByRole("button", { name: "Select all 120 matching calls" }),
    );

    await openMenu(user);
    expect(
      await screen.findByText("Couldn't read the selected calls"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(
      await screen.findByText("2 scenarios × 3 repeats = 6 calls"),
    ).toBeInTheDocument();
    expect(listMatchingCalls).toHaveBeenCalledTimes(2);
  });

  it("sends the trials picked in the menu", async () => {
    const user = userEvent.setup();
    const { onRerunScenarios } = renderTable();
    await openRows(user);
    await user.click(rowBox("refund · Trial 1"));
    await openMenu(user);

    await user.click(screen.getByText("Repeats: 3"));
    await user.click(screen.getByText("1×"));
    expect(
      screen.getByText("1 scenario × 1 repeat = 1 call"),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("menuitem", { name: /Run as a new simulation/ }),
    );

    expect(onRerunScenarios).toHaveBeenCalledWith(["refund"], 1);
  });

  it("lets a re-run within the per-run call limit through", async () => {
    const user = userEvent.setup();
    const { onRerunScenarios } = renderTable();
    await openRows(user);
    await user.click(rowBox("refund · Trial 1"));
    await user.click(rowBox("escalate · Trial 1"));
    await openMenu(user);

    await user.click(screen.getByText("Repeats: 3"));
    await user.click(screen.getByText("Custom…"));
    const custom = screen.getByRole("spinbutton");
    await user.clear(custom);
    await user.type(custom, "20");
    await user.click(screen.getByRole("button", { name: "Set" }));
    expect(
      screen.getByText("2 scenarios × 20 repeats = 40 calls"),
    ).toBeInTheDocument();
    // 2 × 20 is within the limit; the limit is about scenarios × trials.
    expect(screen.queryByText(/a run allows up to/)).not.toBeInTheDocument();
  });

  it("disables the option when scenarios × trials passes 200", async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 70 }, (_, i) =>
      task(`m${i}`, `scenario-${i}`, 1),
    );
    useRunCalls.mockImplementation(() => ({
      tasks: many,
      columns: [],
      groups: [
        {
          label: "All",
          rows: many,
          count: 70,
          measured: 70,
          passed: 0,
          agg: { evals: {} },
        },
      ],
      facets: {},
      count: 70,
      totalPages: 1,
      isLoading: false,
    }));
    const { onRerunScenarios } = renderTable();
    await openRows(user);
    await user.click(pageBox());
    await openMenu(user);

    expect(
      screen.getByText(
        "That is 210 calls; a run allows up to 200. Lower Repeats or select fewer scenarios.",
      ),
    ).toBeInTheDocument();
    const option = screen.getByRole("menuitem", {
      name: /Run as a new simulation/,
    });
    expect(option).toHaveAttribute("aria-disabled", "true");
    await user.click(option);
    expect(onRerunScenarios).not.toHaveBeenCalled();
  });

  it("reads every matching scenario when all matching calls are selected", async () => {
    const user = userEvent.setup();
    total = 120;
    listMatchingCalls.mockResolvedValue({
      callIds: ["r1", "r3", "e1", "t1"],
      scenarioKeys: ["refund", "escalate", "timeout"],
    });
    const { onRerunScenarios } = renderTable();
    await openRows(user);
    await user.click(pageBox());
    await user.click(
      screen.getByRole("button", { name: "Select all 120 matching calls" }),
    );
    await user.click(rowBox("refund · Trial 2"));

    await openMenu(user);
    expect(
      await screen.findByText("3 scenarios × 3 repeats = 9 calls"),
    ).toBeInTheDocument();
    expect(listMatchingCalls).toHaveBeenCalledWith("ex1", {}, ["r2"]);

    await user.click(
      screen.getByRole("menuitem", { name: /Run as a new simulation/ }),
    );
    await waitFor(() =>
      expect(onRerunScenarios).toHaveBeenCalledWith(
        ["refund", "escalate", "timeout"],
        3,
      ),
    );
  });

  it("keeps Re-run off while it has a reason to", async () => {
    const user = userEvent.setup();
    renderTable({ rerunDisabledReason: "Wait for this run to finish" });
    await openRows(user);
    await user.click(rowBox("refund · Trial 1"));

    expect(screen.getByRole("button", { name: /^Re-run/ })).toBeDisabled();
  });

  it("offers only the new simulation when evals can't be re-run here", async () => {
    const user = userEvent.setup();
    renderTable();
    await openRows(user);
    await user.click(rowBox("refund · Trial 1"));
    await openMenu(user);

    expect(
      screen.getByRole("menuitem", { name: /Run as a new simulation/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: /Re-run evals/ }),
    ).not.toBeInTheDocument();
  });

  it("offers only re-running evals when a new simulation can't start", async () => {
    const user = userEvent.setup();
    renderTable({ onRerunScenarios: null, onRerunEvals: vi.fn() });
    await openRows(user);
    await user.click(rowBox("refund · Trial 1"));
    await openMenu(user);

    expect(
      screen.getByRole("menuitem", { name: /Re-run evals/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: /Run as a new simulation/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Repeats:/)).not.toBeInTheDocument();
  });

  it("re-runs evals on exactly the ticked calls, then clears the ticks", async () => {
    const user = userEvent.setup();
    const onRerunEvals = vi.fn().mockResolvedValue({});
    const { onRerunScenarios } = renderTable({ onRerunEvals });
    await openRows(user);
    await user.click(rowBox("refund · Trial 1"));
    await user.click(rowBox("refund · Trial 3"));
    await user.click(rowBox("escalate · Trial 2"));
    await openMenu(user);

    const option = screen.getByRole("menuitem", { name: /Re-run evals/ });
    expect(option).toHaveTextContent(
      "Grades the 3 selected calls again in this run. No new calls.",
    );
    await user.click(option);

    expect(onRerunEvals).toHaveBeenCalledWith(["r1", "r3", "e2"]);
    expect(onRerunScenarios).not.toHaveBeenCalled();
    await waitFor(() => expect(rowBox("refund · Trial 1")).not.toBeChecked());
    expect(screen.queryByText(/selected/)).not.toBeInTheDocument();
  });

  it("keeps the ticks when the eval re-run is refused", async () => {
    const user = userEvent.setup();
    const onRerunEvals = vi.fn().mockRejectedValue(new Error("refused"));
    renderTable({ onRerunEvals });
    await openRows(user);
    await user.click(rowBox("refund · Trial 1"));
    await openMenu(user);

    await user.click(screen.getByRole("menuitem", { name: /Re-run evals/ }));

    expect(onRerunEvals).toHaveBeenCalledTimes(1);
    await new Promise((r) => setTimeout(r, 0));
    expect(rowBox("refund · Trial 1")).toBeChecked();
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("re-runs evals on every matching call but the unticked ones", async () => {
    const user = userEvent.setup();
    total = 120;
    listMatchingCalls.mockResolvedValue({
      callIds: ["r1", "r3", "e1"],
      scenarioKeys: ["refund", "escalate"],
    });
    const onRerunEvals = vi.fn().mockResolvedValue({});
    renderTable({ onRerunEvals });
    await openRows(user);
    await user.click(pageBox());
    await user.click(
      screen.getByRole("button", { name: "Select all 120 matching calls" }),
    );
    await user.click(rowBox("refund · Trial 2"));
    await openMenu(user);

    const option = await screen.findByRole("menuitem", {
      name: /Grades the 3 selected calls/,
    });
    expect(listMatchingCalls).toHaveBeenCalledWith("ex1", {}, ["r2"]);
    await user.click(option);

    expect(onRerunEvals).toHaveBeenCalledWith(["r1", "r3", "e1"]);
  });

  it("holds the eval option while the matching calls are still being read", async () => {
    const user = userEvent.setup();
    total = 120;
    listMatchingCalls.mockImplementation(() => new Promise(() => {}));
    const onRerunEvals = vi.fn();
    renderTable({ onRerunEvals });
    await openRows(user);
    await user.click(pageBox());
    await user.click(
      screen.getByRole("button", { name: "Select all 120 matching calls" }),
    );
    await openMenu(user);

    const option = screen.getByRole("menuitem", { name: /Re-run evals/ });
    expect(option).toHaveAttribute("aria-disabled", "true");
    await user.click(option);
    expect(onRerunEvals).not.toHaveBeenCalled();
  });
});

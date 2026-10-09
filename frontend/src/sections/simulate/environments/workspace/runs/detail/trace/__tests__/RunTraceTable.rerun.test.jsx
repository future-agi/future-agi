import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

const useRunCalls = vi.fn();
vi.mock("src/api/simulate-environments/runDetail", () => ({
  useRunCalls: (...args) => useRunCalls(...args),
}));
const listMatchingScenarioKeys = vi.fn();
vi.mock("src/api/simulate-environments/rerunScenarios", async (orig) => ({
  ...(await orig()),
  listMatchingScenarioKeys: (...args) => listMatchingScenarioKeys(...args),
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
  listMatchingScenarioKeys.mockReset();
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

  it("tells the run page it has calls to tick", () => {
    const onTickableChange = vi.fn();
    renderTable({ onTickableChange });

    expect(onTickableChange).toHaveBeenLastCalledWith(true);
  });

  it("tells the run page when none of its calls can be ticked", () => {
    const untagged = [task("u1", null, null), task("u2", null, null)];
    useRunCalls.mockImplementation(() => ({
      tasks: untagged,
      columns: [],
      groups: [],
      facets: {},
      count: untagged.length,
      totalPages: 1,
      isLoading: false,
    }));
    const onTickableChange = vi.fn();
    renderTable({ onTickableChange });

    expect(onTickableChange).toHaveBeenLastCalledWith(false);
  });

  it("shows no checkbox column for a run with no call to tick, even once filtered", async () => {
    const user = userEvent.setup();
    const untagged = [
      task("u1", null, null),
      task("u2", null, null, "failed"),
    ];
    useRunCalls.mockImplementation((_id, opts = {}) => {
      const status = opts.filters?.status?.[0];
      const tasks = untagged.filter((t) => !status || t.status === status);
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
            { value: "passed", count: 1 },
            { value: "failed", count: 1 },
          ],
        },
        count: tasks.length,
        totalPages: 1,
        isLoading: false,
      };
    });
    renderTable();
    await openRows(user);

    expect(
      screen.queryByRole("checkbox", { name: "Select all calls on this page" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Failed/ }));
    expect(
      screen.queryByRole("checkbox", { name: "Select all calls on this page" }),
    ).not.toBeInTheDocument();
  });

  it("says nothing while a filter decides which calls show", () => {
    useRunCalls.mockImplementation(() => ({
      tasks: [],
      columns: [],
      groups: [],
      facets: {},
      count: 0,
      totalPages: 1,
      isLoading: false,
    }));
    const onTickableChange = vi.fn();
    renderTable({ onTickableChange, initialFilters: { status: ["failed"] } });

    expect(onTickableChange).not.toHaveBeenCalled();
  });

  it("says nothing while the calls are still loading", () => {
    useRunCalls.mockImplementation(() => ({
      tasks: [],
      columns: [],
      groups: [],
      facets: {},
      count: 0,
      totalPages: 1,
      isLoading: true,
    }));
    const onTickableChange = vi.fn();
    renderTable({ onTickableChange });

    expect(onTickableChange).not.toHaveBeenCalled();
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

describe("RunTraceTable — re-running as a new simulation", () => {
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
    expect(screen.getByText("Re-run 2 scenarios")).toBeInTheDocument();
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
    listMatchingScenarioKeys
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishFirst = resolve;
          }),
      )
      .mockResolvedValueOnce(["refund", "escalate"]);
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
    expect(await screen.findByText("Re-run 2 scenarios")).toBeInTheDocument();

    finishFirst(["refund", "escalate", "timeout"]);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByText("Re-run 2 scenarios")).toBeInTheDocument();
  });

  it("offers to try again when the scenarios can't be read", async () => {
    const user = userEvent.setup();
    total = 120;
    listMatchingScenarioKeys
      .mockRejectedValueOnce(new Error("Network Error"))
      .mockResolvedValueOnce(["refund", "escalate"]);
    renderTable();
    await openRows(user);
    await user.click(pageBox());
    await user.click(
      screen.getByRole("button", { name: "Select all 120 matching calls" }),
    );

    await openMenu(user);
    expect(
      await screen.findByText("Couldn't read the selected scenarios"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Re-run 2 scenarios")).toBeInTheDocument();
    expect(listMatchingScenarioKeys).toHaveBeenCalledTimes(2);
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
    await user.click(
      screen.getByRole("menuitem", { name: /Run as a new simulation/ }),
    );

    expect(onRerunScenarios).toHaveBeenCalledWith(["refund", "escalate"], 20);
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
    listMatchingScenarioKeys.mockResolvedValue([
      "refund",
      "escalate",
      "timeout",
    ]);
    const { onRerunScenarios } = renderTable();
    await openRows(user);
    await user.click(pageBox());
    await user.click(
      screen.getByRole("button", { name: "Select all 120 matching calls" }),
    );
    await user.click(rowBox("refund · Trial 2"));

    await openMenu(user);
    expect(await screen.findByText("Re-run 3 scenarios")).toBeInTheDocument();
    expect(listMatchingScenarioKeys).toHaveBeenCalledWith("ex1", {}, ["r2"]);

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
});

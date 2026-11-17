import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { fetchRunCalls } from "src/api/simulate-environments/runCalls";

import FixScenariosTab from "../FixScenariosTab";

vi.mock("src/api/simulate-environments/runCalls", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchRunCalls: vi.fn(),
}));

const call = (key, status) => ({
  id: `call-${key}-${status}`,
  source_scenario_key: key,
  outcome: status,
});

const checkRun = (id) => ({
  run_id: `run-${id}`,
  test_execution_id: `execution-${id}`,
  state: "completed",
});

const renderChecks = (checks) => {
  const callsByExecution = new Map([
    [
      "execution-baseline",
      [
        call("target-A", "failed"),
        call("held-B", "failed"),
        call("must-C", "passed"),
      ],
    ],
    ...checks.map(([id, calls]) => [`execution-${id}`, calls]),
  ]);
  fetchRunCalls.mockImplementation(async (executionId) => {
    const results = callsByExecution.get(executionId);
    if (!results) throw new Error(`Unexpected execution: ${executionId}`);
    return { count: results.length, results };
  });
  const fix = {
    environment_id: "env-1",
    request: {
      target_scenario_keys: ["target-A"],
      held_back_scenario_keys: ["held-B"],
      protected_scenario_keys: ["must-C"],
    },
    check_runs: [checkRun("baseline")],
    candidates: [
      {
        id: "candidate-1",
        outcome: "winner",
        check_runs: checks.map(([id]) => checkRun(id)),
      },
    ],
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <FixScenariosTab fix={fix} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const expectRunLink = (row, runId) => {
  expect(row.getByRole("link", { name: "Open run" })).toHaveAttribute(
    "href",
    `/dashboard/simulate/environments/env-1/runs/run-${runId}/execution-${runId}`,
  );
};

describe("Per scenario run provenance (W10-AGENT-FIX-L06)", () => {
  beforeEach(() => {
    fetchRunCalls.mockReset();
  });

  it("links held-back and must-pass rows to verification, not the target-only check", async () => {
    renderChecks([
      ["target-only", [call("target-A", "passed")]],
      [
        "verification",
        [call("held-B", "passed"), call("must-C", "failed")],
      ],
    ]);

    const held = within(await screen.findByTestId("scenario-held-B"));
    expect(held.getByText("Held back")).toBeInTheDocument();
    expect(held.getByText("Fixed")).toBeInTheDocument();
    expectRunLink(held, "verification");

    const must = within(screen.getByTestId("scenario-must-C"));
    expect(must.getByText("Must-pass")).toBeInTheDocument();
    expect(must.getByText("Broke")).toBeInTheDocument();
    expectRunLink(must, "verification");

    const target = within(screen.getByTestId("scenario-target-A"));
    expect(target.getByText("Fixed")).toBeInTheDocument();
    expectRunLink(target, "target-only");
  });

  it.each([
    ["passed", "failed", "repeat", "Still failing"],
    ["failed", "passed", "verification", "Still failing"],
    ["inconclusive", "passed", "repeat", "Fixed"],
  ])(
    "links repeated results %s then %s to the measured evidence for the outcome",
    async (first, second, expectedRun, expectedOutcome) => {
      renderChecks([
        ["target-only", [call("target-A", "passed")]],
        ["verification", [call("held-B", first)]],
        ["repeat", [call("held-B", second)]],
      ]);

      const held = within(await screen.findByTestId("scenario-held-B"));
      expect(held.getByText(expectedOutcome)).toBeInTheDocument();
      expectRunLink(held, expectedRun);
    },
  );
});

// The baseline and one trial, each read from its own check run; a read in
// `failing` rejects until the test removes it.
const renderTrial = ({ trial, calls, failing = new Set() }) => {
  fetchRunCalls.mockImplementation(async (executionId) => {
    if (failing.has(executionId)) {
      throw { statusCode: 500, detail: "Server error" };
    }
    const results = calls[executionId] ?? [];
    return { count: results.length, results };
  });
  const fix = {
    environment_id: "env-1",
    request: {
      target_scenario_keys: ["target-A"],
      held_back_scenario_keys: ["held-B"],
      protected_scenario_keys: ["must-C"],
    },
    check_runs: [checkRun("baseline")],
    candidates: [trial],
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <FixScenariosTab fix={fix} candidateId={trial.id} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const BASELINE_CALLS = [
  call("target-A", "failed"),
  call("held-B", "failed"),
  call("must-C", "passed"),
];

const expectNoCounts = () => {
  expect(screen.queryByRole("button", { name: /^Broke \d/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Fixed \d/ })).toBeNull();
  expect(screen.queryAllByTestId(/^scenario-/)).toHaveLength(0);
};

describe("Per scenario when results are missing (W10-AGENT-FIX-U07)", () => {
  beforeEach(() => {
    fetchRunCalls.mockReset();
  });

  it.each([["baseline"], ["trial"]])(
    "counts nothing when the %s results fail to load, and reads them on Try again",
    async (side) => {
      const user = userEvent.setup();
      const failing = new Set([`execution-${side}`]);
      renderTrial({
        trial: {
          id: "candidate-2",
          attempt: 2,
          outcome: "rejected",
          check_runs: [checkRun("trial")],
        },
        calls: {
          "execution-baseline": BASELINE_CALLS,
          "execution-trial": [call("target-A", "passed")],
        },
        failing,
      });

      expect(
        await screen.findByText("Could not load Trial 2's results."),
      ).toBeInTheDocument();
      expectNoCounts();

      failing.clear();
      await user.click(screen.getByRole("button", { name: "Try again" }));
      const target = within(await screen.findByTestId("scenario-target-A"));
      expect(target.getByText("Fixed")).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Fixed 1" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByText("Could not load Trial 2's results."),
      ).toBeNull();
    },
  );

  it("says a trial that never ran its checks has no results, instead of zero counts", async () => {
    renderTrial({
      trial: {
        id: "candidate-3",
        attempt: 3,
        outcome: "pending",
        check_runs: [],
      },
      calls: { "execution-baseline": BASELINE_CALLS },
    });

    expect(
      await screen.findByText(
        "Trial 3 has no check results: it stopped before its checks ran.",
      ),
    ).toBeInTheDocument();
    expectNoCounts();
  });

  it("says when a trial's check runs measured nothing to compare", async () => {
    renderTrial({
      trial: {
        id: "candidate-2",
        attempt: 2,
        outcome: "rejected",
        check_runs: [checkRun("trial")],
      },
      calls: {
        "execution-baseline": BASELINE_CALLS,
        "execution-trial": [call("target-A", "inconclusive")],
      },
    });

    expect(
      await screen.findByText(
        "Trial 2's check runs have no scenario results to compare.",
      ),
    ).toBeInTheDocument();
    expectNoCounts();
  });
});

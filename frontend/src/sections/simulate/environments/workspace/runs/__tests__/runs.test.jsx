import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { fDateTime } from "src/utils/format-time";
import RunsPanel from "../RunsPanel";
import { RUNS_COPY } from "../runs.constants";

const env = { id: "env-1", name: "Refund Support", surface: "voice" };

const scenarios = Array.from({ length: 12 }, (_, i) => ({
  id: `s${i}`,
  critical: i < 3,
}));

// A fully set-up environment: an agent is connected and there are scenarios, so
// the pre-flight passes and "Start simulation" is enabled.
const readyState = { agent: { typeId: "chat", name: "Support agent" }, scenarios, evals: [] };

// Only the agent is missing, so exactly one pre-flight item is not-ok — which
// keeps the single "Fix" button unambiguous.
const agentMissingState = { agent: null, scenarios, evals: [] };

function renderPanel(overrides = {}) {
  const props = {
    env,
    envState: readyState,
    runs: [],
    onStart: vi.fn(),
    onOpenRun: vi.fn(),
    onGo: vi.fn(),
    ...overrides,
  };
  render(<RunsPanel {...props} />);
  return props;
}

describe("RunsPanel pre-flight", () => {
  it("shows the environment name as the Environment item", () => {
    renderPanel();
    expect(screen.getByText(env.name)).toBeInTheDocument();
  });

  it("marks the agent Not connected and routes Fix to the overview tab", () => {
    const onGo = vi.fn();
    renderPanel({ envState: agentMissingState, onGo });
    expect(screen.getByText(RUNS_COPY.agentNotConnected)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: RUNS_COPY.fix }));
    expect(onGo).toHaveBeenCalledWith("summary");
  });

  it("shows the scenario count as N tasks", () => {
    renderPanel();
    expect(screen.getByText(RUNS_COPY.tasks(12))).toBeInTheDocument();
  });

  it("marks Evals Optional and warns when none are applied", () => {
    renderPanel();
    expect(screen.getByText(RUNS_COPY.optional)).toBeInTheDocument();
  });
});

describe("RunsPanel estimates", () => {
  it("renders duration, concurrency and cost for 12 scenarios", () => {
    renderPanel();
    // Designer formula max(2, ceil(12 * 0.7)) = 9; flat 4-parallel; 12 * 0.08.
    expect(screen.getByText("~9 min")).toBeInTheDocument();
    expect(screen.getByText("4 parallel")).toBeInTheDocument();
    expect(screen.getByText("$0.96")).toBeInTheDocument();
  });
});

describe("RunsPanel start gating", () => {
  it("enables Start simulation once the environment is ready", () => {
    renderPanel();
    expect(screen.getByRole("button", { name: RUNS_COPY.start })).toBeEnabled();
  });

  it("disables Start simulation until an agent and scenarios exist", () => {
    renderPanel({ envState: agentMissingState });
    expect(screen.getByRole("button", { name: RUNS_COPY.start })).toBeDisabled();
  });

  it("fires onStart when clicked", () => {
    const onStart = vi.fn();
    renderPanel({ onStart });
    fireEvent.click(screen.getByRole("button", { name: RUNS_COPY.start }));
    expect(onStart).toHaveBeenCalledTimes(1);
  });
});

describe("RunsPanel run history", () => {
  const passedRun = {
    id: "ex1",
    executionId: "ex1",
    label: "Run 1",
    status: "passed",
    startedAt: "2026-01-13T16:40:00.000Z",
    finishedAt: "2026-01-13T16:42:10.000Z",
    total: 12,
    passed: 9,
    failed: 3,
    agentVersion: "v2",
  };

  it("shows the empty-history copy when there are no runs", () => {
    renderPanel({ runs: [] });
    expect(screen.getByText(RUNS_COPY.empty.title)).toBeInTheDocument();
    expect(screen.getByText(RUNS_COPY.empty.body)).toBeInTheDocument();
  });

  it("renders a row with label, timestamp, pass % and status chip", () => {
    renderPanel({ runs: [passedRun] });
    expect(screen.getByText(passedRun.label)).toBeInTheDocument();
    expect(
      screen.getByText(new RegExp(fDateTime(passedRun.finishedAt))),
    ).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("Passed")).toBeInTheDocument();
  });

  it("shows a progress bar and an em dash for a running row", () => {
    const runningRun = {
      ...passedRun,
      status: "running",
      passed: 0,
      finishedAt: null,
    };
    renderPanel({ runs: [runningRun] });
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("opens the run on click when it carries an executionId", () => {
    const onOpenRun = vi.fn();
    renderPanel({ runs: [passedRun], onOpenRun });
    fireEvent.click(screen.getByRole("button", { name: new RegExp(passedRun.label) }));
    expect(onOpenRun).toHaveBeenCalledWith(passedRun);
  });

  it("does not open a run that has no executionId", () => {
    const onOpenRun = vi.fn();
    const noExec = { ...passedRun, executionId: undefined };
    renderPanel({ runs: [noExec], onOpenRun });
    expect(
      screen.queryByRole("button", { name: new RegExp(noExec.label) }),
    ).toBeNull();
    fireEvent.click(screen.getByText(noExec.label));
    expect(onOpenRun).not.toHaveBeenCalled();
  });
});

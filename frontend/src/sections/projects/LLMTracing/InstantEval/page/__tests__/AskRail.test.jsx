import React from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";

const history = vi.hoisted(() => ({ runs: [], error: null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../../useInstantEval", async (importOriginal) => ({
  ...(await importOriginal()),
  useInstantEvalHistory: () => ({
    runs: history.runs,
    isLoading: false,
    isError: Boolean(history.error),
    error: history.error,
    refetch: vi.fn(),
  }),
}));
import AskRail from "../AskRail";
import { historyRun } from "../../useInstantEval";

const theme = createTheme({ palette: palette("light") });
const item = (extra = {}) => ({
  taskId: "failed-task",
  name: "Instant eval · Resolved?",
  status: "failed",
  unit: "users",
  rows: 3,
  createdAt: "2026-10-27T00:00:00Z",
  answered: 0,
  pending: 0,
  items: [
    {
      configId: "q1",
      templateId: "evaluator-1",
      question: "Resolved?",
      labels: ["Yes", "No"],
      answerType: "yes_no",
    },
  ],
  counts: {},
  ...extra,
});
function renderRail(onRecreate = vi.fn(), props = {}) {
  const onOpen = vi.fn();
  render(
    <MemoryRouter>
      <ThemeProvider theme={theme}>
        <AskRail
          projectId="p1"
          configsById={{}}
          onToggle={vi.fn()}
          onNew={vi.fn()}
          onOpen={onOpen}
          onRecreate={onRecreate}
          {...props}
        />
      </ThemeProvider>
    </MemoryRouter>,
  );
  return onOpen;
}
beforeEach(() => {
  history.runs = [];
  history.error = null;
});

describe("history recovery", () => {
  it("names a failure before any answers and links directly to fixing the evaluator", () => {
    history.runs = [
      historyRun(
        {
          task_id: "failed-task",
          status: "failed",
          unit: "users",
          rows: 3,
          answered: 3,
          config_ids: ["q1"],
          questions: ["Resolved?"],
          counts: { q1: { __error__: 3 } },
        },
        { q1: item().items[0] },
      ),
    ];
    const onOpen = renderRail();
    const card = screen.getByRole("link", { name: "Open the run: Resolved?" });
    expect(
      within(card).getByText("Failed before answering"),
    ).toBeInTheDocument();
    expect(
      within(card).getByText(
        "No answers were saved. Check the evaluator or recreate the question.",
      ),
    ).toBeInTheDocument();
    const fix = within(card).getByRole("link", { name: "Fix evaluator" });
    expect(fix).toHaveAttribute("href", "/dashboard/evaluations/evaluator-1");
    fireEvent.keyDown(fix, { key: "Enter" });
    fireEvent.click(fix);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("offers question recreation instead of retrying a deleted evaluator", () => {
    const run = item();
    delete run.items[0].templateId;
    history.runs = [run];
    const onRecreate = vi.fn();
    const onOpen = renderRail(onRecreate);
    fireEvent.click(screen.getByRole("button", { name: "Recreate question" }));
    expect(onRecreate).toHaveBeenCalledExactlyOnceWith(run);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("keeps saved answers from a partially failed multi-question run", () => {
    history.runs = [
      item({
        answered: 0,
        items: [...item().items, { ...item().items[0], configId: "q2" }],
        counts: { q1: { Yes: 3 }, q2: { __error__: 3 } },
      }),
    ];
    renderRail();
    expect(screen.getByText("Failed · 3 answers saved")).toBeInTheDocument();
    expect(screen.queryByText("Failed before answering")).toBeNull();
  });

  it("uses answer counts instead of a certainty-looking percentage", () => {
    history.runs = [
      item({ status: "completed", answered: 3, counts: { q1: { Yes: 3 } } }),
    ];
    renderRail();
    expect(screen.getByText("3 Yes")).toBeInTheDocument();
    expect(screen.queryByText("Yes 100%")).toBeNull();
  });
});

describe("the open run's card while it is answered", () => {
  it("counts from the page's summary, not from History's own poll", () => {
    // History last polled at 8 rows; the page's summary says 2.
    history.runs = [
      item({
        taskId: "live-task",
        status: "running",
        unit: "sessions",
        rows: 49,
        answered: 8,
        pending: 41,
        counts: { q1: { Yes: 6, No: 2 } },
      }),
    ];
    renderRail(vi.fn(), {
      selectedId: "live-task",
      openProgress: {
        taskId: "live-task",
        settled: 2,
        total: 49,
        counts: { q1: { Yes: 3, No: 1 } },
      },
    });
    const card = screen.getByRole("link", { name: "Open the run: Resolved?" });
    expect(within(card).getByText(/2 of 49 answered/)).toBeInTheDocument();
    expect(within(card).getByText("3 Yes")).toBeInTheDocument();
    expect(within(card).queryByText(/8 of 49/)).toBeNull();
  });

  it("counts a run of several questions as its page does, not as answers", () => {
    // Run 9e0df0dd: its bar has question 1's 11 answers while 6 rows are
    // done with both questions.
    history.runs = [
      item({
        taskId: "live-task",
        status: "running",
        unit: "sessions",
        rows: 49,
        answered: 6,
        pending: 43,
        items: [...item().items, { ...item().items[0], configId: "q2" }],
        counts: { q1: { Yes: 1, No: 10 }, q2: { Yes: 7 } },
      }),
      item({
        taskId: "three-task",
        status: "running",
        unit: "sessions",
        rows: 49,
        answered: 4,
        pending: 45,
        items: [
          { ...item().items[0], question: "Three?" },
          { ...item().items[0], configId: "q2" },
          { ...item().items[0], configId: "q3" },
        ],
      }),
    ];
    renderRail(vi.fn(), {
      selectedId: "live-task",
      openProgress: {
        taskId: "live-task",
        settled: 6,
        total: 49,
        counts: { q1: { Yes: 1, No: 10 }, q2: { Yes: 7 } },
        status: "running",
      },
    });
    const card = screen.getByRole("link", { name: "Open the run: Resolved?" });
    expect(
      within(card).getByText(/6 of 49 done with both/),
    ).toBeInTheDocument();
    expect(within(card).queryByText(/6 of 49 answered/)).toBeNull();
    expect(within(card).getByText("10 No")).toBeInTheDocument();
    const three = screen.getByRole("link", { name: "Open the run: Three?" });
    expect(
      within(three).getByText(/4 of 49 done with all 3/),
    ).toBeInTheDocument();
  });

  it("keeps History's count for a run that isn't open", () => {
    history.runs = [
      item({
        taskId: "live-task",
        status: "running",
        unit: "sessions",
        rows: 49,
        answered: 8,
        pending: 41,
      }),
    ];
    renderRail(vi.fn(), {
      selectedId: "other-task",
      openProgress: { taskId: "other-task", settled: 2, total: 49 },
    });
    expect(screen.getByText(/8 of 49 answered/)).toBeInTheDocument();
  });
});

describe("History that can't be read", () => {
  it("offers no retry for a project that is gone", () => {
    history.error = { statusCode: 404, result: "Project not found" };
    renderRail();
    expect(screen.getByText("No past questions to show.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("offers a retry for a read that failed on the way", () => {
    history.error = { statusCode: 503 };
    renderRail();
    expect(
      screen.getByText(/Past questions couldn't be loaded/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

describe("where a run that isn't done stands", () => {
  const ago = (ms) => new Date(Date.now() - ms).toISOString();
  const liveRun = (taskId, question, lastActivityAt) =>
    item({
      taskId,
      status: "running",
      unit: "sessions",
      rows: 49,
      answered: 8,
      pending: 41,
      lastActivityAt,
      items: [{ ...item().items[0], configId: taskId, question }],
    });
  const card = (question) =>
    screen.getByRole("link", { name: `Open the run: ${question}` });

  afterEach(() => vi.useRealTimers());

  it("takes the open run's state from its page", () => {
    // History still says running; the page knows its workflow is gone.
    history.runs = [liveRun("open-task", "Resolved?", ago(5_000))];
    renderRail(vi.fn(), {
      selectedId: "open-task",
      openProgress: {
        taskId: "open-task",
        settled: 8,
        total: 49,
        counts: {},
        status: "stalled",
      },
    });
    const open = card("Resolved?");
    expect(within(open).getByText(/Stopped · 8 of 49 answered/)).toBeTruthy();
    expect(
      within(open).getByRole("img", { name: "Stopped before finishing" }),
    ).toBeInTheDocument();
    expect(
      within(open).queryByRole("img", { name: "Still being answered" }),
    ).toBeNull();
  });

  it("says another live run hasn't moved, from when it last did", () => {
    history.runs = [
      liveRun("quiet-task", "Quiet?", ago(5 * 60_000)),
      liveRun("busy-task", "Busy?", ago(10_000)),
    ];
    renderRail();
    const quiet = card("Quiet?");
    expect(
      within(quiet).getByText(/No progress lately · 8 of 49 answered/),
    ).toBeInTheDocument();
    expect(
      within(quiet).getByRole("img", { name: "No progress lately" }),
    ).toBeInTheDocument();
    const busy = card("Busy?");
    expect(within(busy).getByText(/8 of 49 answered/)).toBeInTheDocument();
    expect(within(busy).queryByText(/No progress lately/)).toBeNull();
    expect(
      within(busy).getByRole("img", { name: "Still being answered" }),
    ).toBeInTheDocument();
  });

  it("turns quiet as time passes, with no new poll", () => {
    vi.useFakeTimers();
    history.runs = [liveRun("busy-task", "Busy?", ago(80_000))];
    renderRail();
    expect(
      within(card("Busy?")).getByRole("img", { name: "Still being answered" }),
    ).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(15_000);
    });
    expect(
      within(card("Busy?")).getByRole("img", { name: "No progress lately" }),
    ).toBeInTheDocument();
  });
});

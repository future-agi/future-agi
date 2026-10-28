import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";

const history = vi.hoisted(() => ({ runs: [] }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../../useInstantEval", async (importOriginal) => ({
  ...(await importOriginal()),
  useInstantEvalHistory: () => ({
    runs: history.runs,
    isLoading: false,
    isError: false,
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

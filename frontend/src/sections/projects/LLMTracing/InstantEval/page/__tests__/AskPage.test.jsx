/* eslint-disable react/prop-types */
import React from "react";
import PropTypes from "prop-types";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import { askLink } from "src/sections/projects/observeHandoff";

const fakes = vi.hoisted(() => ({
  startAsk: vi.fn(),
  runs: [],
  // useAskRun's answer, by task id; a run being created has none.
  askRuns: {},
  askRunArgs: [],
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("../../results/KeepAnswering", () => ({ default: () => null }));
vi.mock("../../useJevAvailability", () => ({
  useJevAvailability: () => ({ available: true, isLoading: false }),
}));
vi.mock("../../useInstantEval", async (importOriginal) => ({
  ...(await importOriginal()),
  startAsk: fakes.startAsk,
  useInstantEvals: () => ({ all: [], items: [], isLoading: false }),
  useInstantEvalHistory: () => ({
    runs: fakes.runs,
    isLoading: false,
    isError: false,
    hasMore: false,
    loadMore: () => {},
    refetch: () => {},
  }),
}));
vi.mock("../useAskRun", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useAskRun: (args) => {
      fakes.askRunArgs.push(args);
      const { taskId, unit, seedItems, questions, startedAt } = args;
      return (
        fakes.askRuns[taskId] || {
          status: "running",
          startedAt,
          elapsedMs: 0,
          answered: 0,
          total: seedItems?.length || 0,
          questions: (questions || []).map((q) => ({ configId: "", ...q })),
          records: actual.mergeRecords([], seedItems || [], unit),
          window: null,
          unit,
          reads: null,
          refetch: () => {},
        }
      );
    },
  };
});
// The charts are B's; here they only stand in for a click on them.
vi.mock("../viz", async (importOriginal) => ({
  ...(await importOriginal()),
  AskTimeline: ({ records, onSelect, onFocus, questions }) => (
    <div data-testid="timeline">
      <span>{records.length} squares</span>
      <span>
        {records.filter((r) => !Object.keys(r.answers).length).length} not yet
      </span>
      <button
        type="button"
        onClick={() => onSelect(["s1", "s2"], "Tue Oct 13, 2–4 PM")}
      >
        Select the outage
      </button>
      <button
        type="button"
        onClick={() =>
          onFocus({ configId: questions[0].configId, answer: "No" })
        }
      >
        Focus No
      </button>
    </div>
  ),
  AskPatterns: () => null,
  AskHeatmap: () => null,
}));

import AskPage from "../AskPage";

const PROJECT = "p1";
const CONFIG = "config-1";
const QUESTION = "Did the agent resolve the customer's issue?";
const POPULATION = [
  { id: "s1", time: "2026-10-13T14:10:00Z", user_id: "cust_ada" },
  { id: "s2", time: "2026-10-13T15:20:00Z", user_id: "cust_ben" },
  { id: "s3", time: "2026-10-14T09:00:00Z", user_id: "cust_kai" },
];

const theme = createTheme({ palette: palette("light") });

function Where() {
  const location = useLocation();
  return (
    <output data-testid="where">{location.pathname + location.search}</output>
  );
}

const where = () => {
  const url = new URL(
    screen.getByTestId("where").textContent,
    "http://app.test",
  );
  return { path: url.pathname, params: Object.fromEntries(url.searchParams) };
};

function renderAt(path) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Shell = ({ children }) => (
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>{children}</ThemeProvider>
    </QueryClientProvider>
  );
  Shell.propTypes = { children: PropTypes.node };
  return render(
    <Shell>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/dashboard/observe/:observeId/ask"
            element={
              <>
                <AskPage />
                <Where />
              </>
            }
          />
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </Shell>,
  );
}

const answered = (id, time, user, answer) => ({
  id,
  unit: "sessions",
  time,
  user_id: user,
  end_user_id: "",
  label: id,
  excerpt: `Where is my order? (${id})`,
  trace_id: null,
  facets: {
    model: "gpt-4o-mini",
    turns: 1,
    duration_ms: 2000,
    error: answer === "No",
  },
  answers: {
    [CONFIG]: { answer, confidence: 0.9, explanation: "", status: "completed" },
  },
});

const doneRun = {
  status: "done",
  startedAt: "2026-10-15T10:00:00Z",
  elapsedMs: 21000,
  answered: 3,
  total: 3,
  questions: [
    {
      configId: CONFIG,
      question: QUESTION,
      answerType: "yes_no",
      labels: ["Yes", "No"],
    },
  ],
  records: [
    answered("s3", "2026-10-14T09:00:00Z", "cust_kai", "Yes"),
    answered("s2", "2026-10-13T15:20:00Z", "cust_ben", "No"),
    answered("s1", "2026-10-13T14:10:00Z", "cust_ada", "No"),
  ],
  window: ["2026-10-13T14:10:00Z", "2026-10-14T09:00:00Z"],
  unit: "sessions",
  reads: "transcript",
  refetch: () => {},
};

const historyItem = (taskId, unit, question) => ({
  taskId,
  name: `Instant eval · ${question}`,
  status: "completed",
  createdAt: new Date().toISOString(),
  unit,
  reads: "trace",
  rows: 50,
  items: [
    {
      configId: `${taskId}-config`,
      question,
      answerType: "yes_no",
      labels: ["Yes", "No"],
    },
  ],
  counts: { [`${taskId}-config`]: { Yes: 25, No: 25 } },
  answered: 50,
  pending: 0,
});

beforeEach(() => {
  fakes.startAsk.mockReset();
  fakes.runs = [];
  fakes.askRuns = {};
  fakes.askRunArgs = [];
  globalThis.sessionStorage?.clear?.();
  globalThis.localStorage?.clear?.();
});

describe("the Ask page", () => {
  it("asks about the rows handed over and opens the run, every row showing at once", async () => {
    let finishStart;
    fakes.startAsk.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishStart = resolve;
        }),
    );
    // Evaluate → Ask a question on Sessions, Past 7 days.
    renderAt(
      askLink(PROJECT, {
        unit: "sessions",
        label: "Past 7D",
        population: POPULATION,
        filters: [],
      }),
    );
    expect(where().params).toMatchObject({
      new: "1",
      unit: "sessions",
      tab: "sessions",
    });
    expect(
      screen.getByRole("heading", { name: "Ask Jev about these sessions" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/3 sessions handed over from Past 7 days/),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "Question 1" }), {
      target: { value: QUESTION },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Ask Jev about 3 sessions" }),
    );

    // While the task is created, every square is there, none read yet.
    expect(
      screen.getByText("Saving the question and starting Jev…"),
    ).toBeInTheDocument();
    expect(screen.getByText("3 squares")).toBeInTheDocument();
    expect(screen.getByText("3 not yet")).toBeInTheDocument();
    expect(fakes.startAsk).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: PROJECT,
        unit: "sessions",
        handedOver: true,
        items: POPULATION,
        questions: [expect.objectContaining({ question: QUESTION })],
      }),
    );

    await act(async () => {
      finishStart({
        taskId: "task-1",
        questions: doneRun.questions,
        items: POPULATION,
      });
    });
    // The run counts from the click, with the rows it was asked about.
    const launched = fakes.askRunArgs
      .filter((args) => args.taskId === "task-1")
      .at(-1);
    expect(launched.startedAt).toBe(fakes.askRunArgs[0].startedAt);
    expect(launched.startedAt).toBeTruthy();
    expect(launched.seedItems).toEqual(POPULATION);
    // The run's own address, still on the Sessions tab.
    expect(where().params).toEqual({
      run: "task-1",
      unit: "sessions",
      tab: "sessions",
    });
    expect(screen.getByRole("heading", { name: QUESTION })).toBeInTheDocument();
    expect(
      screen.getByText(/Sessions · Past 7 days · 3 sessions/),
    ).toBeInTheDocument();
  });

  it("keeps the question as typed when Jev can't start", async () => {
    fakes.startAsk.mockRejectedValue(new Error("boom"));
    renderAt(askLink(PROJECT, { unit: "sessions", population: POPULATION }));
    fireEvent.change(screen.getByRole("textbox", { name: "Question 1" }), {
      target: { value: QUESTION },
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Ask Jev about 3 sessions" }),
      );
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Question 1" })).toHaveValue(
      QUESTION,
    );
    expect(where().params.new).toBe("1");
  });

  it("opens a past run from History on its own view's tab", () => {
    fakes.runs = [
      historyItem("task-traces", "traces", "Did the user get a refund?"),
      historyItem("task-sessions", "sessions", QUESTION),
    ];
    fakes.askRuns["task-sessions"] = doneRun;
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-traces&unit=traces&tab=traces`,
    );

    // Narrow windows start with History closed.
    fireEvent.click(
      screen.getByRole("button", { name: "Show past questions" }),
    );
    fireEvent.click(
      screen.getByRole("link", { name: `Open the run: ${QUESTION}` }),
    );
    expect(where().params).toEqual({
      run: "task-sessions",
      unit: "sessions",
      tab: "sessions",
    });
    expect(screen.getByText("3 sessions answered in 21 s")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: `Open the run: ${QUESTION}` }),
    ).toHaveAttribute("aria-current", "page");

    // + New question starts on the same view.
    fireEvent.click(screen.getByRole("button", { name: /New question/ }));
    expect(where().params).toEqual({
      new: "1",
      unit: "sessions",
      tab: "sessions",
    });
  });

  it("puts a run opened by its link alone on its own view's tab", () => {
    fakes.askRuns["task-traces"] = {
      ...doneRun,
      unit: "traces",
      knownUnit: "traces",
    };
    renderAt(`/dashboard/observe/${PROJECT}/ask?run=task-traces`);
    expect(where().params).toEqual({
      run: "task-traces",
      unit: "traces",
      tab: "traces",
    });
  });

  it("opens the latest run when nothing is chosen", () => {
    fakes.runs = [historyItem("task-sessions", "sessions", QUESTION)];
    fakes.askRuns["task-sessions"] = doneRun;
    renderAt(`/dashboard/observe/${PROJECT}/ask?tab=sessions`);
    expect(where().params).toEqual({
      run: "task-sessions",
      unit: "sessions",
      tab: "sessions",
    });
  });

  it("narrows the rows to a focused answer or a selection, and clears", () => {
    fakes.runs = [historyItem("task-sessions", "sessions", QUESTION)];
    fakes.askRuns["task-sessions"] = doneRun;
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    const table = () =>
      screen.getByRole("region", { name: "Sessions asked about" });
    const rows = () => within(table()).getAllByRole("row").length - 1;
    expect(rows()).toBe(3);

    fireEvent.click(screen.getByRole("button", { name: "Focus No" }));
    expect(rows()).toBe(2);
    expect(within(table()).getByText(`No · ${QUESTION}`)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Select the outage" }));
    expect(rows()).toBe(2);
    const bar = screen.getByRole("region", { name: "Selected rows" });
    expect(within(bar).getByText(/2 sessions/)).toBeInTheDocument();
    expect(
      within(bar).getByRole("link", { name: /Show in Traces/ }),
    ).toHaveAttribute("href", expect.stringContaining("session_id"));

    fireEvent.click(within(bar).getByRole("button", { name: "Clear" }));
    expect(rows()).toBe(3);
    expect(screen.queryByRole("region", { name: "Selected rows" })).toBeNull();
  });
});

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

// The same stand-ins as AskPage.test.jsx: the page's hooks answer from
// `fakes`, and the charts and drawers are stubs.
const fakes = vi.hoisted(() => ({
  startAskRun: vi.fn(),
  estimate: vi.fn(),
  runs: [],
  askRuns: {},
  askRunArgs: [],
  rowsArgs: [],
  control: vi.fn(),
  denied: new Set(),
  keep: [],
  rowsTotal: null,
  saved: [],
  historyError: null,
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureDenied: (feature) => fakes.denied.has(feature),
}));
vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  default: ({ onClose }) => (
    <div>
      <button type="button" onClick={onClose}>
        Close panel
      </button>
    </div>
  ),
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: ({ traceId }) => (
    <div role="dialog" aria-label="Trace drawer">
      <span>Trace {traceId}</span>
    </div>
  ),
}));
vi.mock("../../results/KeepAnswering", () => ({
  default: (props) => {
    fakes.keep.push(props);
    return <span>Keep answering new traces</span>;
  },
}));
vi.mock("../../useJevAvailability", () => ({
  useJevAvailability: () => ({ available: true, isLoading: false }),
}));
vi.mock("../../useInstantEval", async (importOriginal) => ({
  ...(await importOriginal()),
  startAskRun: fakes.startAskRun,
  estimateAsk: fakes.estimate,
  useInstantEvals: () => ({
    all: fakes.saved,
    items: fakes.saved,
    isLoading: false,
  }),
  useInstantEvalHistory: () => ({
    runs: fakes.runs,
    isLoading: false,
    isError: Boolean(fakes.historyError),
    error: fakes.historyError,
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
      const { taskId, unit, questions, startedAt } = args;
      return (
        fakes.askRuns[taskId] || {
          taskId: taskId || null,
          status: "loading",
          summary: null,
          startedAt,
          elapsedMs: 0,
          answered: 0,
          failed: 0,
          pending: 0,
          notRead: 0,
          total: 0,
          questions: (questions || []).map((q) => ({ configId: "", ...q })),
          records: null,
          sample: null,
          window: null,
          unit,
          reads: null,
          refetch: () => {},
          control: fakes.control,
        }
      );
    },
  };
});
vi.mock("../useAskRows", () => ({
  useAskRows: (args) => {
    fakes.rowsArgs.push(args);
    return {
      rows: [],
      total: fakes.rowsTotal ?? 0,
      isLoading: false,
      hasMore: false,
      isFetchingMore: false,
      loadMore: () => {},
    };
  },
  fetchRowIds: vi.fn(async () => []),
  downloadRowsCsv: vi.fn(),
}));
vi.mock("../viz", async (importOriginal) => ({
  ...(await importOriginal()),
  AskTimeline: () => <div data-testid="timeline" />,
  AskPatterns: () => null,
  AskHeatmap: () => null,
}));

import AskPage from "../AskPage";

const PROJECT = "p1";
const QUESTION = "Did the agent resolve the customer's issue?";
const QUESTIONS = [
  {
    configId: "config-1",
    question: QUESTION,
    answerType: "yes_no",
    labels: ["Yes", "No"],
  },
];
const POPULATION = [
  { id: "s1", time: "2026-10-13T14:10:00Z", user_id: "cust_ada" },
  { id: "s2", time: "2026-10-13T15:20:00Z", user_id: "cust_ben" },
  { id: "s3", time: "2026-10-14T09:00:00Z", user_id: "cust_kai" },
];
// AskPage.test.jsx's estimate of a view of `count` rows handed over.
const smallEstimate = (count) => ({
  population: count,
  population_is_lower_bound: false,
  rows_to_read: 1000,
  questions: 1,
  credits_estimate: 60,
  seconds_estimate: 210,
  needs_confirm: false,
  max_rows: 100000,
  confirm_rows: 5000,
  default_sample_size: null,
  sessions_per_user: null,
  credits_per_answer: 0.06,
  choices: [
    {
      sample_size: null,
      rows_to_read: count,
      seconds_estimate: 10 + count * 0.2,
      needs_confirm: count > 5000,
      allowed: true,
    },
  ],
});

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

beforeEach(() => {
  fakes.startAskRun.mockReset();
  fakes.estimate.mockReset();
  fakes.estimate.mockResolvedValue(smallEstimate(3));
  fakes.control.mockReset();
  fakes.runs = [];
  fakes.askRuns = {};
  fakes.askRunArgs = [];
  fakes.rowsArgs = [];
  fakes.denied = new Set();
  fakes.keep = [];
  fakes.rowsTotal = null;
  fakes.saved = QUESTIONS;
  fakes.historyError = null;
  globalThis.sessionStorage?.clear?.();
  globalThis.localStorage?.clear?.();
});

// The server's refusal when the organization is at its active-run limit
// (tracer/views/instant_eval_scale.py _busy_response): the runs it names
// come with their task and project ids.
const BUSY = {
  message:
    "Your organization already has 2 Instant eval runs in progress, the most it runs at once. Visible runs: “Was it resolved?” (1,000 rows, started by Ada); “Did it escalate?” (200 rows, started by Ben). Pause or cancel a run you can access, or wait for one to finish.",
  error_code: "INSTANT_EVAL_BUSY",
  runs: [
    {
      task_id: "task-busy-1",
      project_id: PROJECT,
      question: "Was it resolved?",
      rows: 1000,
      started_by: "Ada",
      started_at: "2026-11-09T09:00:00Z",
    },
    {
      task_id: "task-busy-2",
      project_id: "p2",
      question: "Did it escalate?",
      rows: 200,
      started_by: "Ben",
      started_at: "2026-11-09T09:05:00Z",
    },
  ],
};

describe("a start refused because the organization's runs are all in use", () => {
  it("links to each run in flight, in a new tab, and keeps the question typed", async () => {
    const { AskBusy } = await import("../../useInstantEval");
    fakes.startAskRun.mockRejectedValue(new AskBusy(BUSY));
    renderAt(askLink(PROJECT, { unit: "sessions", population: POPULATION }));
    fireEvent.change(
      await screen.findByRole("textbox", { name: "Question 1" }),
      { target: { value: QUESTION } },
    );
    await screen.findByText(/3 selected sessions/);
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Ask Jev about 3 sessions" }),
      );
    });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/started by Ada/);
    const here = within(alert).getByRole("link", {
      name: "Open “Was it resolved?” in a new tab",
    });
    expect(here).toHaveAttribute(
      "href",
      `/dashboard/observe/${PROJECT}/ask?run=task-busy-1`,
    );
    expect(here).toHaveAttribute("target", "_blank");
    // A run in another project is reachable too: the rail lists only this
    // project's runs.
    const elsewhere = within(alert).getByRole("link", {
      name: "Open “Did it escalate?” in a new tab",
    });
    expect(elsewhere).toHaveAttribute(
      "href",
      "/dashboard/observe/p2/ask?run=task-busy-2",
    );
    expect(elsewhere).toHaveAttribute("target", "_blank");
    expect(within(alert).getAllByRole("link")).toHaveLength(2);
    // The composer stays as it was, to ask again once a run is paused.
    expect(screen.getByRole("textbox", { name: "Question 1" })).toHaveValue(
      QUESTION,
    );
    expect(where().params.new).toBe("1");
  });

  it("links only the runs it can open", async () => {
    const { AskBusy } = await import("../../useInstantEval");
    fakes.startAskRun.mockRejectedValue(
      new AskBusy({
        ...BUSY,
        runs: [
          { ...BUSY.runs[0], question: "" },
          { ...BUSY.runs[1], task_id: "" },
          { ...BUSY.runs[1], task_id: "task-busy-3", project_id: null },
        ],
      }),
    );
    renderAt(askLink(PROJECT, { unit: "sessions", population: POPULATION }));
    fireEvent.change(
      await screen.findByRole("textbox", { name: "Question 1" }),
      { target: { value: QUESTION } },
    );
    await screen.findByText(/3 selected sessions/);
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Ask Jev about 3 sessions" }),
      );
    });
    const links = within(screen.getByRole("alert")).getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAccessibleName("Open the run in a new tab");
    expect(links[0]).toHaveAttribute(
      "href",
      `/dashboard/observe/${PROJECT}/ask?run=task-busy-1`,
    );
  });
});

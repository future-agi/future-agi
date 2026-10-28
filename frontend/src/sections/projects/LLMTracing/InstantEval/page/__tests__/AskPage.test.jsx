/* eslint-disable react/prop-types */
import React from "react";
import PropTypes from "prop-types";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import axios, { endpoints } from "src/utils/axios";
import { askLink, readHandoff } from "src/sections/projects/observeHandoff";

const fakes = vi.hoisted(() => ({
  startAskRun: vi.fn(),
  estimate: vi.fn(),
  runs: [],
  // useAskRun's answer, by task id; a run being created has none.
  askRuns: {},
  askRunArgs: [],
  rowsArgs: [],
  control: vi.fn(),
  // Features the deployment's license or plan denies.
  denied: new Set(),
  // What Keep answering was given, each time it rendered.
  keep: [],
  // The rows a filter selects, when more than the run's records.
  rowsTotal: null,
  saved: [],
  // Why History couldn't be read, when it couldn't.
  historyError: null,
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureDenied: (feature) => fakes.denied.has(feature),
}));
// The views' filter panel: here it applies a Status filter, as a pick in it
// would.
vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  default: ({ onApply, onClose, currentFilters }) => (
    <div>
      <button
        type="button"
        onClick={() =>
          onApply([
            ...(currentFilters || []),
            {
              field: "status",
              fieldCategory: "system",
              fieldType: "string",
              apiColType: "SYSTEM_METRIC",
              operator: "equals",
              value: "ERROR",
            },
          ])
        }
      >
        Pick status ERROR
      </button>
      <button type="button" onClick={onClose}>
        Close panel
      </button>
    </div>
  ),
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ traceId, onNext, onPrev, hasNext, hasPrev }) => (
    <div role="dialog" aria-label="Trace drawer">
      <span>Trace {traceId}</span>
      <button type="button" disabled={!hasPrev} onClick={onPrev}>
        Previous trace
      </button>
      <button type="button" disabled={!hasNext} onClick={onNext}>
        Next trace
      </button>
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
// The rows the table pages through, filtered as the charts select.
vi.mock("../useAskRows", () => ({
  useAskRows: (args) => {
    fakes.rowsArgs.push(args);
    const run = Object.values(fakes.askRuns).find(
      (item) => item.taskId === args.taskId,
    );
    const all = run?.records || [];
    const { filter } = args;
    const rows = all.filter(
      (record) =>
        (!filter?.answer ||
          record.answers[filter.config_id]?.answer === filter.answer) &&
        (!filter?.time_from ||
          (record.time >= filter.time_from && record.time < filter.time_to)),
    );
    return {
      rows,
      total: fakes.rowsTotal ?? rows.length,
      isLoading: false,
      hasMore: false,
      isFetchingMore: false,
      loadMore: () => {},
    };
  },
  fetchRowIds: vi.fn(async () => []),
  downloadRowsCsv: vi.fn(),
}));
// The charts are B's; here they only stand in for a click on them.
vi.mock("../viz", async (importOriginal) => ({
  ...(await importOriginal()),
  AskTimeline: ({ records, summary, onSelect, onFocus, questions }) => (
    <div data-testid="timeline">
      <span>{records ? `${records.length} squares` : "columns"}</span>
      <span>{summary ? `${summary.total} counted` : "nothing counted"}</span>
      <button
        type="button"
        onClick={() =>
          onSelect(
            {
              time_from: "2026-10-13T14:00:00Z",
              time_to: "2026-10-13T16:00:00Z",
            },
            "Tue Oct 13, 2–4 PM",
          )
        }
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
import { fetchRowIds } from "../useAskRows";
import {
  presetWindow,
  viewSeed,
  windowItem,
} from "src/sections/projects/observeScope";

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

const QUESTIONS = [
  {
    configId: CONFIG,
    question: QUESTION,
    answerType: "yes_no",
    labels: ["Yes", "No"],
  },
];

const runOf = ({
  status = "done",
  total = 3,
  answered: done = 3,
  failed = 0,
  sample = null,
  records,
} = {}) => ({
  taskId: "task-sessions",
  status,
  startedAt: "2026-10-15T10:00:00Z",
  elapsedMs: 21000,
  answered: done,
  failed,
  pending: total - done - failed,
  notRead: 0,
  total,
  rate: status === "running" ? 152 : null,
  eta: status === "running" ? 240 : null,
  createdBy: { name: "Nikhil", email: "n@example.com" },
  questions: QUESTIONS,
  records:
    records === undefined
      ? [
          answered("s3", "2026-10-14T09:00:00Z", "cust_kai", "Yes"),
          answered("s2", "2026-10-13T15:20:00Z", "cust_ben", "No"),
          answered("s1", "2026-10-13T14:10:00Z", "cust_ada", "No"),
        ]
      : records,
  sample,
  summary: {
    total,
    credits_estimate: 0.18,
    counts: { [CONFIG]: { Yes: 620, No: 380, __error__: failed } },
    sample,
    scope: { filters: [], sample_size: sample?.requested ?? null, ids: null },
  },
  window: ["2026-10-13T14:10:00Z", "2026-10-14T09:00:00Z"],
  unit: "sessions",
  reads: "transcript",
  tz: "UTC",
  refetch: () => {},
  control: fakes.control,
});

const doneRun = runOf();

// The server's refusal of a question on Jev without the organization's
// TypeSafe key (tracer/views/instant_eval_scale.py _jev_key_missing).
const KEY_MISSING = {
  message:
    "API key not configured for typesafe. Please add your API key in settings.",
  error_code: "JEV_KEY_MISSING",
  settings_path: "/dashboard/settings/ai-providers",
};
const AI_PROVIDERS = "/dashboard/settings/ai-providers";

const ESTIMATE = {
  population: 104233,
  population_is_lower_bound: false,
  rows_to_read: 1000,
  questions: 1,
  credits_estimate: 60,
  seconds_estimate: 210,
  needs_confirm: false,
  max_rows: 100000,
  confirm_rows: 5000,
  default_sample_size: 1000,
  sessions_per_user: null,
  // Cloud: each answer is billed at the server's rate.
  credits_per_answer: 0.06,
  choices: [
    {
      sample_size: 500,
      rows_to_read: 500,
      seconds_estimate: 110,
      needs_confirm: false,
      allowed: true,
    },
    {
      sample_size: 1000,
      rows_to_read: 1000,
      seconds_estimate: 210,
      needs_confirm: false,
      allowed: true,
    },
    {
      sample_size: 2000,
      rows_to_read: 2000,
      seconds_estimate: 410,
      needs_confirm: false,
      allowed: true,
    },
    {
      sample_size: 5000,
      rows_to_read: 5000,
      seconds_estimate: 1010,
      needs_confirm: false,
      allowed: true,
    },
    {
      sample_size: null,
      rows_to_read: 104233,
      seconds_estimate: 20857,
      needs_confirm: true,
      allowed: false,
    },
  ],
};

const smallEstimate = (count) => ({
  ...ESTIMATE,
  population: count,
  default_sample_size: null,
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
  fakes.startAskRun.mockReset();
  fakes.estimate.mockReset();
  fakes.estimate.mockResolvedValue(ESTIMATE);
  fakes.control.mockReset();
  fakes.control.mockResolvedValue({ status: "paused", message: "Paused." });
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

describe("a new question the server holds for its confirm", () => {
  it("confirms the server's count from the composer, then starts", async () => {
    const { AskNeedsConfirm } = await import("../../useInstantEval");
    fakes.startAskRun
      .mockRejectedValueOnce(
        new AskNeedsConfirm({
          message: "Confirm to start.",
          rows_to_read: 6000,
          credits_estimate: 360,
        }),
      )
      .mockResolvedValue({ taskId: "task-new", questions: QUESTIONS });
    renderAt(askLink(PROJECT, { unit: "sessions", label: "Past 7D" }));
    await screen.findByText(/104,233 sessions in the Sessions view/);
    fireEvent.change(screen.getByRole("textbox", { name: "Question 1" }), {
      target: { value: QUESTION },
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", {
          name: "Ask Jev about a sample of 1,000 sessions",
        }),
      );
    });
    // Not a plain error: the server's numbers, and a way to confirm them.
    expect(
      screen.getByText(/This reads 6,000 sessions: about 360 credits\./),
    ).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Start" }));
    });
    expect(fakes.startAskRun).toHaveBeenCalledTimes(2);
    expect(fakes.startAskRun).toHaveBeenLastCalledWith(
      expect.objectContaining({ confirm: true, confirmedRows: 6000 }),
    );
    expect(where().params.run).toBe("task-new");
  });

  it("drops the server's confirm when the unit changes", async () => {
    const { AskNeedsConfirm } = await import("../../useInstantEval");
    fakes.startAskRun.mockRejectedValueOnce(
      new AskNeedsConfirm({
        message: "Confirm to start.",
        rows_to_read: 6000,
        credits_estimate: 360,
      }),
    );
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?new=1&unit=sessions&tab=sessions`,
    );
    await screen.findByText(/104,233 sessions in the Sessions view/);
    fireEvent.change(screen.getByRole("textbox", { name: "Question 1" }), {
      target: { value: QUESTION },
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", {
          name: "Ask Jev about a sample of 1,000 sessions",
        }),
      );
    });
    expect(screen.getByText(/This reads 6,000 sessions/)).toBeInTheDocument();
    // Its Start would launch the sessions question on the Traces view.
    fireEvent.click(screen.getByRole("button", { name: "Traces" }));
    await screen.findByText(/104,233 traces in the Traces view/);
    expect(screen.queryByText(/This reads 6,000/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Start" })).toBeNull();
    expect(fakes.startAskRun).toHaveBeenCalledTimes(1);
  });

  const holdTheSample = async () => {
    const { AskNeedsConfirm } = await import("../../useInstantEval");
    fakes.startAskRun.mockRejectedValueOnce(
      new AskNeedsConfirm({
        message: "Confirm to start.",
        rows_to_read: 6000,
        credits_estimate: 360,
      }),
    );
    renderAt(askLink(PROJECT, { unit: "sessions", label: "Past 7D" }));
    await screen.findByText(/104,233 sessions in the Sessions view/);
    fireEvent.change(screen.getByRole("textbox", { name: "Question 1" }), {
      target: { value: QUESTION },
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", {
          name: "Ask Jev about a sample of 1,000 sessions",
        }),
      );
    });
    expect(screen.getByText(/This reads 6,000 sessions/)).toBeInTheDocument();
  };

  it("drops the server's confirm when the sample size changes", async () => {
    await holdTheSample();
    // Its Start would launch the sample of 1,000 under a picker showing 2,000.
    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Sample size/ }));
    fireEvent.click(
      await screen.findByRole("option", { name: "2,000 sessions" }),
    );
    expect(screen.queryByText(/This reads 6,000/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Start" })).toBeNull();
    expect(
      screen.getByRole("button", {
        name: "Ask Jev about a sample of 2,000 sessions",
      }),
    ).toBeEnabled();
    expect(fakes.startAskRun).toHaveBeenCalledTimes(1);
  });

  it("drops the server's confirm when what Jev reads changes", async () => {
    await holdTheSample();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Jev reads/ }));
    fireEvent.click(
      await screen.findByRole("option", { name: /The conversation only/ }),
    );
    expect(screen.queryByText(/This reads 6,000/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Start" })).toBeNull();
    expect(fakes.startAskRun).toHaveBeenCalledTimes(1);
  });
});

// jsdom can't match :focus-visible, so read the rule emotion wrote for the
// element's own class.
function focusRing(element) {
  const rules = Array.from(document.styleSheets).flatMap((sheet) =>
    Array.from(sheet.cssRules, (rule) => rule.cssText),
  );
  return rules.find((rule) =>
    Array.from(element.classList).some((name) =>
      rule.startsWith(`.${name}:focus-visible`),
    ),
  );
}

describe("the Ask page", () => {
  it("reads a sample of a big view by default and opens the run", async () => {
    let finishStart;
    fakes.startAskRun.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishStart = resolve;
        }),
    );
    const filters = [
      {
        column_id: "created_at",
        filter_config: {
          filter_type: "datetime",
          filter_op: "between",
          filter_value: [
            "2026-10-08T00:00:00.000Z",
            "2026-10-15T00:00:00.000Z",
          ],
        },
      },
    ];
    renderAt(
      askLink(PROJECT, {
        unit: "sessions",
        label: "Custom range",
        dateOption: "Custom",
        filters,
      }),
    );
    expect(where().params).toMatchObject({ new: "1", tab: "sessions" });
    expect(
      await screen.findByText(/104,233 sessions in the Sessions view/),
    ).toBeInTheDocument();
    // The server counted the view with its own filters.
    expect(fakes.estimate).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: PROJECT,
        unit: "sessions",
        filters,
      }),
    );
    const sample = screen.getByRole("radio", { name: /A sample of 1,000/ });
    expect(sample).toHaveAttribute("aria-checked", "true");
    expect(
      within(sample).getByText(/Reads 1,000 sessions · about 60 credits/),
    ).toBeInTheDocument();
    // Every row is over the limit for one run.
    expect(screen.getByRole("radio", { name: /All 104,233/ })).toHaveAttribute(
      "aria-disabled",
      "true",
    );

    fireEvent.change(screen.getByRole("textbox", { name: "Question 1" }), {
      target: { value: QUESTION },
    });
    fireEvent.click(
      screen.getByRole("button", {
        name: "Ask Jev about a sample of 1,000 sessions",
      }),
    );
    expect(
      screen.getByText("Saving the question and starting Jev…"),
    ).toBeInTheDocument();
    expect(fakes.startAskRun).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: PROJECT,
        unit: "sessions",
        filters,
        sampleSize: 1000,
        confirm: false,
        questions: [expect.objectContaining({ question: QUESTION })],
      }),
    );

    await act(async () => {
      finishStart({ taskId: "task-1", questions: QUESTIONS });
    });
    const launched = fakes.askRunArgs
      .filter((args) => args.taskId === "task-1")
      .at(-1);
    expect(launched.startedAt).toBe(fakes.askRunArgs[0].startedAt);
    expect(where().params).toEqual({
      run: "task-1",
      unit: "sessions",
      tab: "sessions",
    });
    expect(screen.getByRole("heading", { name: QUESTION })).toBeInTheDocument();
  });

  it("picks a sample size from the keyboard", async () => {
    renderAt(askLink(PROJECT, { unit: "sessions", label: "Past 7D" }));
    await screen.findByText(/104,233 sessions in the Sessions view/);
    const select = screen.getByRole("combobox", { name: /Sample size/ });
    select.focus();
    fireEvent.keyDown(select, { key: "ArrowDown" });
    const option = await screen.findByRole("option", {
      name: "2,000 sessions",
    });
    // Enter on the menu's option must not reach the card's own Enter
    // handler, which would re-choose the old size.
    fireEvent.keyDown(option, { key: "Enter" });
    expect(
      screen.getByRole("radio", { name: /A sample of 2,000/ }),
    ).toHaveAttribute("aria-checked", "true");
  });

  it("rings the answer type and Ask Jev when they have keyboard focus", async () => {
    renderAt(askLink(PROJECT, { unit: "sessions", label: "Past 7D" }));
    await screen.findByText(/104,233 sessions in the Sessions view/);
    for (const control of [
      screen.getByRole("button", { name: "Yes / No" }),
      screen.getByRole("button", { name: "Categories" }),
      screen.getByRole("button", {
        name: "Ask Jev about a sample of 1,000 sessions",
      }),
      // The size cards had their ring already.
      screen.getByRole("radio", { name: /A sample of 1,000/ }),
    ])
      expect(focusRing(control)).toMatch(/outline: 2px solid/);
  });

  it("restates the credits before reading every row of a big view", async () => {
    fakes.estimate.mockResolvedValue({
      ...ESTIMATE,
      population: 20000,
      choices: [
        ...ESTIMATE.choices.slice(0, 4),
        {
          sample_size: null,
          rows_to_read: 20000,
          seconds_estimate: 4010,
          needs_confirm: true,
          allowed: true,
        },
      ],
    });
    fakes.startAskRun.mockResolvedValue({
      taskId: "task-2",
      questions: QUESTIONS,
    });
    renderAt(askLink(PROJECT, { unit: "sessions", filters: [] }));
    fireEvent.click(await screen.findByRole("radio", { name: /All 20,000/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "Question 1" }), {
      target: { value: QUESTION },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Ask Jev about 20,000 sessions" }),
    );
    expect(fakes.startAskRun).not.toHaveBeenCalled();
    expect(
      screen.getByText(/Read 20,000 sessions\? That is about 1,200 credits/),
    ).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Start" }));
    });
    expect(fakes.startAskRun).toHaveBeenCalledWith(
      expect.objectContaining({ sampleSize: null, confirm: true }),
    );
  });

  it("asks about exactly the rows handed over", async () => {
    fakes.estimate.mockResolvedValue(smallEstimate(3));
    fakes.startAskRun.mockResolvedValue({
      taskId: "task-3",
      questions: QUESTIONS,
    });
    renderAt(
      askLink(PROJECT, {
        unit: "sessions",
        label: "Past 7D",
        population: POPULATION,
        filters: [],
      }),
    );
    expect(
      await screen.findByText(/3 selected sessions from Past 7 days/),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Question 1" }), {
      target: { value: QUESTION },
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Ask Jev about 3 sessions" }),
      );
    });
    expect(fakes.startAskRun).toHaveBeenCalledWith(
      expect.objectContaining({ ids: ["s1", "s2", "s3"], sampleSize: null }),
    );
  });

  it("names the runs in flight when the organization is at its limit", async () => {
    const { AskBusy } = await import("../../useInstantEval");
    fakes.estimate.mockResolvedValue(smallEstimate(3));
    fakes.startAskRun.mockRejectedValue(
      new AskBusy({
        message:
          "Your organization already has 2 Instant eval runs in progress, the most it runs at once: “Was it resolved?” (1,000 rows, started by Ada).",
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
    expect(screen.getByRole("alert")).toHaveTextContent(/started by Ada/);
    expect(screen.getByRole("textbox", { name: "Question 1" })).toHaveValue(
      QUESTION,
    );
    expect(where().params.new).toBe("1");
  });

  it("offers the largest sample the credits left this month pay for", async () => {
    fakes.estimate.mockResolvedValue({
      ...ESTIMATE,
      credit_check: {
        allowed: false,
        error_code: "FREE_TIER_LIMIT",
        current_usage: 970,
        limit: 1000,
        credits_left: 30,
        blocked: false,
        message:
          "This run needs about 60 credits; you have 30 left this month.",
      },
      credit_max_sample: 500,
    });
    renderAt(askLink(PROJECT, { unit: "sessions" }));
    await screen.findByRole("radio", { name: /A sample of 1,000/ });
    const needs =
      /This run needs about 60 credits; you have 30 left this month\./;
    expect(screen.getByText(needs)).toBeInTheDocument();
    // Reading every row needs more still; why is said outside the faded,
    // disabled choice.
    const why = screen.getByText(/needs about 6,254 credits; you have 30 left/);
    expect(why.closest("[aria-disabled='true']")).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: "Question 1" }), {
      target: { value: QUESTION },
    });
    expect(
      screen.getByRole("button", {
        name: "Ask Jev about a sample of 1,000 sessions",
      }),
    ).toBeDisabled();
    // The way forward stays usable on the card that can't be chosen.
    const all = screen.getByRole("radio", { name: /All 104,233/ });
    expect(all).toHaveAttribute("aria-disabled", "true");
    const offers = screen.getAllByRole("button", {
      name: "Read a sample of 500 sessions instead",
    });
    expect(offers).toHaveLength(2);
    for (const offer of offers) {
      expect(offer).toBeEnabled();
      expect(offer.closest("[aria-disabled='true']")).toBeNull();
    }
    fireEvent.click(offers[0]);
    const fitted = screen.getByRole("radio", { name: /A sample of 500/ });
    expect(fitted).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByText(needs)).toBeNull();
    expect(
      screen.getByRole("button", {
        name: "Ask Jev about a sample of 500 sessions",
      }),
    ).toBeEnabled();
  });

  it("says what a pause budget allows and offers billing with the sample that fits", async () => {
    fakes.estimate.mockResolvedValue({
      ...ESTIMATE,
      credit_check: {
        allowed: false,
        error_code: "BUDGET_LIMIT",
        current_usage: 70,
        limit: 100,
        credits_left: 30,
        blocked: false,
        message:
          "This run needs about 60 credits; your budget limit allows 30 more this month.",
      },
      credit_max_sample: 500,
    });
    renderAt(askLink(PROJECT, { unit: "sessions" }));
    await screen.findByRole("radio", { name: /A sample of 1,000/ });
    expect(
      screen.getByText(
        /This run needs about 60 credits; your budget limit allows 30 more this month\./,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/you have 30 left/)).toBeNull();
    const offers = screen.getAllByRole("button", {
      name: "Read a sample of 500 sessions instead",
    });
    expect(offers).toHaveLength(2);
    const links = screen.getAllByRole("link", { name: "Open billing" });
    expect(links).toHaveLength(2);
    for (const link of links) {
      expect(link).toHaveAttribute("href", "/dashboard/settings/billing");
      expect(link.closest("[aria-disabled='true']")).toBeNull();
    }
  });

  it("links to billing outside the card when no sample fits", async () => {
    fakes.estimate.mockResolvedValue({
      ...ESTIMATE,
      credit_check: {
        allowed: false,
        error_code: "FREE_TIER_LIMIT",
        current_usage: 1000,
        limit: 1000,
        credits_left: 0,
        blocked: false,
        message:
          "This run needs about 60 credits; you have none left this month.",
      },
      credit_max_sample: 0,
    });
    renderAt(askLink(PROJECT, { unit: "sessions" }));
    const all = await screen.findByRole("radio", { name: /All 104,233/ });
    expect(all).toHaveAttribute("aria-disabled", "true");
    const links = screen.getAllByRole("link", { name: "Open billing" });
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link).toHaveAttribute("href", "/dashboard/settings/billing");
      expect(link.closest("[aria-disabled='true']")).toBeNull();
    }
    expect(
      screen.getAllByText(/you have none left this month\./).length,
    ).toBeGreaterThan(0);
  });

  it("prints a run's credits under 1 to a tenth", () => {
    fakes.askRuns["task-sessions"] = { ...runOf(), creditsUsed: 0.72 };
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(screen.getByText(/· 0\.7 credits/)).toBeInTheDocument();
    expect(screen.queryByText(/\b1 credits\b/)).toBeNull();
  });

  it("says why billing refused a start and links to billing", async () => {
    const { AskNoCredits } = await import("../../useInstantEval");
    fakes.estimate.mockResolvedValue(smallEstimate(3));
    fakes.startAskRun.mockRejectedValue(
      new AskNoCredits({
        message:
          "Your subscription has unpaid invoices. Update your payment method in billing to run this.",
        error_code: "PAYMENT_REQUIRED",
        credits_estimate: 0.18,
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
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/unpaid invoices/);
    expect(
      within(alert).getByRole("link", { name: "Open billing" }),
    ).toHaveAttribute("href", "/dashboard/settings/billing");
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

  it("walks the table's traces with the drawer's Next and Previous", () => {
    fakes.askRuns["task-traces"] = {
      ...doneRun,
      unit: "traces",
      knownUnit: "traces",
    };
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-traces&unit=traces&tab=traces`,
    );
    const table = screen.getByRole("region", { name: "Traces asked about" });
    const [first, second] = within(table).getAllByRole("row").slice(1);
    fireEvent.click(first);
    const drawer = screen.getByRole("dialog", { name: "Trace drawer" });
    const shown = () => within(drawer).getByText(/^Trace /).textContent;
    const start = shown();
    expect(
      within(drawer).getByRole("button", { name: "Previous trace" }),
    ).toBeDisabled();
    fireEvent.click(within(drawer).getByRole("button", { name: "Next trace" }));
    expect(shown()).not.toBe(start);
    fireEvent.click(
      within(drawer).getByRole("button", { name: "Previous trace" }),
    );
    expect(shown()).toBe(start);
    expect(second).toBeTruthy();
  });

  it("narrows the rows to a focused answer or a selection, and clears", () => {
    fakes.runs = [historyItem("task-sessions", "sessions", QUESTION)];
    fakes.askRuns["task-sessions"] = doneRun;
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    // A run of a few rows draws a square each; the counts come with it.
    expect(screen.getByText("3 squares")).toBeInTheDocument();
    expect(screen.getByText("3 counted")).toBeInTheDocument();
    const table = () =>
      screen.getByRole("region", { name: "Sessions asked about" });
    const rows = () => within(table()).getAllByRole("row").length - 1;
    expect(rows()).toBe(3);

    fireEvent.click(screen.getByRole("button", { name: "Focus No" }));
    expect(fakes.rowsArgs.at(-1).filter).toEqual({
      config_id: CONFIG,
      answer: "No",
    });
    expect(rows()).toBe(2);
    expect(within(table()).getByText(`No · ${QUESTION}`)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Select the outage" }));
    expect(fakes.rowsArgs.at(-1).filter).toEqual({
      time_from: "2026-10-13T14:00:00Z",
      time_to: "2026-10-13T16:00:00Z",
    });
    expect(rows()).toBe(2);
    expect(within(table()).getByText("Tue Oct 13, 2–4 PM")).toBeInTheDocument();
    const bar = screen.getByRole("region", { name: "Selected rows" });
    expect(within(bar).getByText(/2 sessions/)).toBeInTheDocument();

    fireEvent.click(within(bar).getByRole("button", { name: "Clear" }));
    expect(rows()).toBe(3);
    expect(screen.queryByRole("region", { name: "Selected rows" })).toBeNull();
  });

  // Insights is Enterprise: without it Find patterns is marked and opens the
  // Insights gate, without reading the rows first.
  it("sends a locked Find patterns to the Insights gate", async () => {
    fakes.denied = new Set(["insights"]);
    fetchRowIds.mockClear();
    fakes.runs = [historyItem("task-sessions", "sessions", QUESTION)];
    fakes.askRuns["task-sessions"] = doneRun;
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    fireEvent.click(screen.getByRole("button", { name: "Focus No" }));
    const bar = screen.getByRole("region", { name: "Selected rows" });
    const patterns = within(bar).getByRole("button", {
      name: /Find patterns/,
    });
    expect(
      within(patterns).getByRole("img", { name: "Enterprise feature" }),
    ).toBeInTheDocument();
    fireEvent.click(patterns);
    await waitFor(() =>
      expect(where().path).toBe(`/dashboard/observe/${PROJECT}/insights`),
    );
    expect(fetchRowIds).not.toHaveBeenCalled();
  });

  it("estimates the whole view from a sample and reads more of it", async () => {
    const sample = {
      population: 104233,
      population_is_lower_bound: false,
      requested: 1000,
      read: 1000,
      is_sample: true,
      can_extend: true,
      next_size: 1000,
    };
    fakes.askRuns["task-sessions"] = runOf({
      total: 1000,
      answered: 1000,
      sample,
      records: null,
    });
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    // Too many rows for squares: the charts draw the counts.
    expect(screen.getByText("columns")).toBeInTheDocument();
    expect(screen.getByText("≈ 38% No (95% CI 35–41%)")).toBeInTheDocument();
    expect(
      screen.getByText(/1,000 of 104,233 sessions read/),
    ).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Read 1,000 more" }));
    });
    expect(fakes.control).toHaveBeenCalledWith("read_more", { more: 1000 });
  });

  it("explains a read-more refused at the credit cap, with a billing link", async () => {
    const sample = {
      population: 104233,
      population_is_lower_bound: false,
      requested: 1000,
      read: 1000,
      is_sample: true,
      can_extend: true,
      next_size: 1000,
    };
    fakes.askRuns["task-sessions"] = runOf({
      total: 1000,
      answered: 1000,
      sample,
      records: null,
    });
    const message =
      "This run needs about 60 credits; you have 12 left this month. A sample of up to 200 sessions fits.";
    fakes.control.mockRejectedValueOnce({
      response: {
        status: 403,
        data: {
          result: {
            message,
            error_code: "FREE_TIER_LIMIT",
            credits_estimate: 60,
            credits_left: 12,
            sample_size: 200,
          },
        },
      },
    });
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Read 1,000 more" }));
    });
    expect(screen.getByText(message, { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open billing" })).toHaveAttribute(
      "href",
      "/dashboard/settings/billing",
    );
  });

  it("says how far the rows Jev couldn't answer could move a sample's estimate", async () => {
    const sample = {
      population: 24473,
      population_is_lower_bound: false,
      requested: 1000,
      read: 1000,
      is_sample: true,
      can_extend: true,
      next_size: 1000,
    };
    fakes.askRuns["task-sessions"] = runOf({
      total: 1107,
      answered: 1000,
      failed: 107,
      sample,
      records: null,
    });
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(
      screen.getByText(
        /^107 of 1,107 couldn't be answered\. If they were all Yes or all No, No would be \d+–\d+%\.$/,
      ),
    ).toBeInTheDocument();
    // The retry sits with the bounds, once.
    const retries = screen.getAllByRole("button", { name: "Retry 107 rows" });
    expect(retries).toHaveLength(1);
    await act(async () => {
      fireEvent.click(retries[0]);
    });
    expect(fakes.control).toHaveBeenCalledWith("retry_failed", {});
  });

  it("pauses a live run and retries the rows that failed", async () => {
    fakes.askRuns["task-sessions"] = runOf({
      status: "running",
      total: 3000,
      answered: 1200,
      failed: 30,
      records: null,
    });
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(
      screen.getByText(
        /Answering 1,230 of 3,000 · 152 \/ min · about 4 min left/,
      ),
    ).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    });
    expect(fakes.control).toHaveBeenCalledWith("pause", {});
    // A live run retries its failures once it is done.
    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();
  });

  it("resumes a paused run before it retries or reads more", () => {
    fakes.runs = [historyItem("task-sessions", "sessions", QUESTION)];
    // 400 rows still unread: a retry or "read more" would restart the run
    // and read all of them.
    fakes.askRuns["task-sessions"] = runOf({
      status: "paused",
      total: 1000,
      answered: 570,
      failed: 30,
    });
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Read all/ })).toBeNull();
  });

  it("asks before it cancels, since a cancelled run can't be resumed", async () => {
    const live = runOf({
      status: "running",
      total: 49,
      answered: 8,
      records: null,
    });
    live.summary = { ...live.summary, counts: { [CONFIG]: { Yes: 5, No: 3 } } };
    fakes.askRuns["task-sessions"] = live;
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(fakes.control).not.toHaveBeenCalled();
    const ask = screen
      .getByText(
        "Cancel this run? The 8 answers so far are kept; it can't be resumed.",
      )
      .closest("[role='alert']");
    // Back keeps it running.
    fireEvent.click(within(ask).getByRole("button", { name: "Back" }));
    expect(screen.queryByText(/Cancel this run\?/)).toBeNull();
    expect(fakes.control).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
    });
    expect(fakes.control).toHaveBeenCalledExactlyOnceWith("cancel", {});
    expect(screen.queryByText(/Cancel this run\?/)).toBeNull();
  });

  it("pauses at once, without asking", async () => {
    fakes.askRuns["task-sessions"] = runOf({
      status: "running",
      total: 49,
      answered: 8,
      records: null,
    });
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    });
    expect(fakes.control).toHaveBeenCalledExactlyOnceWith("pause", {});
    expect(screen.queryByText(/Cancel this run\?/)).toBeNull();
  });

  it("says a pause is draining, and when a run waits for its workers", () => {
    const pausing = runOf({ status: "pausing", total: 1000, answered: 410 });
    pausing.summary = { ...pausing.summary, in_flight: 6 };
    fakes.askRuns["task-sessions"] = pausing;
    const { unmount } = renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(
      screen.getByText(
        /Pausing… 6 sessions still being answered · 410 of 1,000 read/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
    unmount();

    const waiting = runOf({ status: "running", total: 1000, answered: 410 });
    waiting.summary = { ...waiting.summary, waiting_s: 185 };
    fakes.askRuns["task-sessions"] = waiting;
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(
      screen.getByText(
        /Waiting for workers… no answer for 3 min · 410 of 1,000 answered/,
      ),
    ).toBeInTheDocument();
  });

  it("retries the rows that failed once a run is done", async () => {
    fakes.askRuns["task-sessions"] = runOf({
      total: 3000,
      answered: 2970,
      failed: 30,
      records: null,
    });
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(
      screen.getByText(/30 rows couldn't be fully answered/),
    ).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Retry 30 rows" }));
    });
    expect(fakes.control).toHaveBeenCalledWith("retry_failed", {});
  });
});

describe("a run whose project was deleted", () => {
  const NOT_FOUND = { statusCode: 404, result: "Project not found" };
  const gone = () => ({
    ...runOf({ total: 0, answered: 0, records: null }),
    status: "error",
    summary: null,
    questions: [],
    error: NOT_FOUND,
  });

  it("says so once, without a retry that can't help", () => {
    fakes.askRuns["task-sessions"] = gone();
    fakes.historyError = NOT_FOUND;
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(
      screen.getByText(
        "This run can't be opened. It or its project was deleted, or it belongs to another organization.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.queryByText(/Answering 0 of 0/)).toBeNull();
    expect(screen.queryByText(/couldn't be loaded/)).toBeNull();
  });

  it("keeps the retry for a read that failed on the way", () => {
    fakes.askRuns["task-sessions"] = {
      ...gone(),
      error: { statusCode: 503 },
    };
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(screen.getByText(/This run couldn't be loaded/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("says so with no run chosen, rather than inviting a first question", () => {
    fakes.historyError = NOT_FOUND;
    renderAt(`/dashboard/observe/${PROJECT}/ask?unit=sessions&tab=sessions`);
    expect(
      screen.getByText(
        "This project's questions can't be opened. It was deleted, or it belongs to another organization.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Ask Jev a question about each row")).toBeNull();
  });
});

describe("a run paused before it picked its rows", () => {
  it("says so and offers Resume", async () => {
    // Paused 2 s after Start: the server counts every row it asked for as
    // still to read, and marks it as not picked yet.
    const paused = runOf({
      status: "paused",
      total: 115,
      answered: 0,
      records: null,
    });
    paused.summary = { ...paused.summary, preparing: true, counts: {} };
    fakes.askRuns["task-sessions"] = paused;
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(
      screen.getByText("Paused before picking the 115 sessions to read"),
    ).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    });
    expect(fakes.control).toHaveBeenCalledWith("resume", {});
  });
});

describe("a run that answered nothing", () => {
  const KEY_REJECTED =
    "TypeSafe rejected the API key saved under Settings → AI Providers. Update the key and try again.";
  const allFailed = (failure) => {
    const run = runOf({ total: 5, answered: 0, failed: 5, records: null });
    run.summary = {
      ...run.summary,
      counts: { [CONFIG]: { __error__: 5 } },
      failure,
    };
    return run;
  };

  it("reads as failed, says why, links to the fix and retries", async () => {
    fakes.askRuns["task-sessions"] = allFailed({
      message: KEY_REJECTED,
      every: true,
      ai_providers: true,
    });
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(
      screen.getByText("Couldn't answer any of the 5 sessions"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/sessions answered in/)).toBeNull();
    expect(screen.queryByText(/couldn't be fully answered/)).toBeNull();
    const alert = screen.getByText(KEY_REJECTED).closest("[role='alert']");
    expect(
      within(alert).getByRole("link", { name: "Open AI Providers" }),
    ).toHaveAttribute("href", AI_PROVIDERS);
    // One retry, beside the reason.
    const retries = screen.getAllByRole("button", { name: "Retry 5 rows" });
    expect(retries).toHaveLength(1);
    expect(alert).toContainElement(retries[0]);
    await act(async () => {
      fireEvent.click(retries[0]);
    });
    expect(fakes.control).toHaveBeenCalledWith("retry_failed", {});
  });

  it("names the reason most rows give, without a settings link it doesn't need", () => {
    fakes.askRuns["task-sessions"] = allFailed({
      message: "TypeSafe didn't respond in time. Try again.",
      every: false,
      ai_providers: false,
    });
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(
      screen.getByText(
        "TypeSafe didn't respond in time. Try again. Some rows failed for another reason; open a row to see why.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Open AI Providers" }),
    ).toBeNull();
  });
});

describe("a run asking several questions while it is answered", () => {
  it("says its progress counts rows done with every question", () => {
    const run = runOf({
      status: "running",
      total: 49,
      answered: 2,
      records: null,
    });
    run.questions = [
      ...QUESTIONS,
      {
        configId: "config-2",
        question: "What was the request about?",
        answerType: "category",
        labels: ["Billing", "Shipping"],
      },
    ];
    fakes.askRuns["task-sessions"] = run;
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(
      screen.getByText(
        /^Answering · 2 of 49 sessions done with both questions · 152 \/ min/,
      ),
    ).toBeInTheDocument();
  });

  it("says whose answers the headline counts, and how many that question has", () => {
    // Run 9e0df0dd at 5 s: "1 Yes · 10 No" for question 1 beside
    // "6 of 49 sessions done with both questions".
    const run = runOf({
      status: "running",
      total: 49,
      answered: 6,
      records: null,
    });
    run.questions = [
      ...QUESTIONS,
      {
        configId: "config-2",
        question: "Was the customer polite?",
        answerType: "yes_no",
        labels: ["Yes", "No"],
      },
    ];
    run.summary = {
      ...run.summary,
      counts: {
        [CONFIG]: { Yes: 1, No: 10, __error__: 0 },
        "config-2": { Yes: 7, __error__: 0 },
      },
    };
    fakes.askRuns["task-sessions"] = run;
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(screen.getByLabelText("Answer summary")).toHaveTextContent(
      /^1 Yes · 10 No$/,
    );
    expect(
      screen.getByText(
        "These counts are for question 1: 11 of 49 sessions answered so far.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/6 of 49 sessions done with both questions/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /^2 · / }));
    expect(screen.getByLabelText("Answer summary")).toHaveTextContent(
      /^7 Yes$/,
    );
    expect(
      screen.getByText(
        "These counts are for question 2: 7 of 49 sessions answered so far.",
      ),
    ).toBeInTheDocument();
  });

  it("names no question when it asked one", () => {
    fakes.askRuns["task-sessions"] = runOf({
      status: "running",
      total: 49,
      answered: 6,
      records: null,
    });
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(screen.getByLabelText("Answer summary")).toBeInTheDocument();
    expect(screen.queryByText(/These counts are for question/)).toBeNull();
  });
});

describe("a run whose workflow went before it picked its rows", () => {
  it("says it stopped, offers Resume and claims no rows read", async () => {
    // The server stops calling it "picking" once its workflow is gone.
    const stalled = runOf({
      status: "stalled",
      total: 1000,
      answered: 0,
      sample: {
        is_sample: true,
        population: 24473,
        requested: 1000,
        read: 1000,
        next_size: 1000,
        can_extend: false,
      },
      records: null,
    });
    stalled.summary = { ...stalled.summary, preparing: true, counts: {} };
    fakes.askRuns["task-sessions"] = stalled;
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(
      screen.getByText(
        "This run stopped before it picked the 1,000 sessions to read",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Picking the/)).toBeNull();
    expect(screen.queryByText(/sessions read, picked at random/)).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    });
    expect(fakes.control).toHaveBeenCalledWith("resume", {});
  });
});

describe("a run that stopped before finishing", () => {
  it("says how far it got and resumes", async () => {
    fakes.askRuns["task-sessions"] = runOf({
      status: "stalled",
      total: 3000,
      answered: 1200,
      records: null,
    });
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(
      screen.getByText(
        "This run stopped before finishing: 1,200 of 3,000 answered",
      ),
    ).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    });
    expect(fakes.control).toHaveBeenCalledWith("resume", {});
  });
});

describe("a start that fails", () => {
  const start = async () => {
    fakes.estimate.mockResolvedValue(smallEstimate(3));
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
  };

  it.each([
    [
      "a 503",
      {
        status: false,
        statusCode: 503,
        message: "Database temporarily unavailable",
      },
    ],
    ["a 500", { status: false, statusCode: 500, message: "Server Error" }],
    [
      "no answer at all",
      { message: "Network Error", transportCode: "ERR_NETWORK" },
    ],
  ])(
    "on the server's side (%s) asks to try again, not to fix the question",
    async (_, failure) => {
      fakes.startAskRun.mockRejectedValue(failure);
      await start();
      const alert = screen.getByRole("alert");
      expect(alert).toHaveTextContent(
        "Couldn't start right now. Try again in a moment.",
      );
      expect(alert).not.toHaveTextContent(/Check the question/);
      // Ready to try again as it was.
      expect(screen.getByRole("textbox", { name: "Question 1" })).toHaveValue(
        QUESTION,
      );
      expect(
        screen.getByRole("button", { name: "Ask Jev about 3 sessions" }),
      ).toBeEnabled();
      expect(where().params.new).toBe("1");
    },
  );

  it("shows a refusal's own message, else asks to check the question", async () => {
    // As the app's axios hands over the server's error envelope.
    const message = "Those questions aren't saved in this project.";
    fakes.startAskRun.mockRejectedValue({
      status: false,
      code: "bad_request",
      detail: message,
      message,
      error: message,
      result: message,
      statusCode: 400,
    });
    await start();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Those questions aren't saved in this project.",
    );
  });

  it("asks to check the question for a refusal with no words of its own", async () => {
    fakes.startAskRun.mockRejectedValue({ status: false, statusCode: 400 });
    await start();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Jev couldn't start. Check the question and try again.",
    );
  });

  it("points to AI Providers when Jev's TypeSafe key is missing", async () => {
    const { askError } = await import("../../useInstantEval");
    // As startAskRun hands over the server's 400.
    fakes.startAskRun.mockRejectedValue(
      askError({
        status: false,
        code: "JEV_KEY_MISSING",
        message: KEY_MISSING.message,
        statusCode: 400,
        result: KEY_MISSING,
      }),
    );
    await start();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(KEY_MISSING.message);
    expect(
      within(alert).getByRole("link", { name: "Open AI Providers" }),
    ).toHaveAttribute("href", AI_PROVIDERS);
    // The question stays as typed, ready once the key is added.
    expect(screen.getByRole("textbox", { name: "Question 1" })).toHaveValue(
      QUESTION,
    );
    expect(where().params.new).toBe("1");
  });

  it("says a Users count is still under way as the server words it", async () => {
    const { AskRefused } = await import("../../useInstantEval");
    fakes.startAskRun.mockRejectedValue(
      new AskRefused({
        message:
          "Still counting the users these filters match; start again to carry on.",
      }),
    );
    await start();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Still counting the users these filters match; start again to carry on.",
    );
  });
});

describe("a cancelled run, and controls the server refuses", () => {
  const SAMPLE = {
    is_sample: true,
    population: 24473,
    requested: 1000,
    read: 360,
    next_size: 1000,
    can_extend: true,
  };
  const OPEN = `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`;
  const refusal = (code, message) =>
    Object.assign(new Error(message), {
      response: {
        status: 409,
        data: { result: { message, error_code: code } },
      },
    });

  it("is asked again, not resumed, retried or read further", async () => {
    fakes.askRuns["task-sessions"] = runOf({
      status: "cancelled",
      total: 1000,
      answered: 340,
      failed: 20,
      sample: { ...SAMPLE, can_extend: false },
      records: null,
    });
    fakes.startAskRun.mockResolvedValue({
      taskId: "task-again",
      questions: QUESTIONS,
    });
    renderAt(OPEN);
    expect(
      screen.getByText("Cancelled · 340 of 1,000 answered"),
    ).toBeInTheDocument();
    for (const name of [
      /^Resume$/,
      /Read the rest/,
      /^Retry \d/,
      /^Read [\d,]+ more/,
      /^Read all/,
    ])
      expect(screen.queryByRole("button", { name })).toBeNull();
    const again = screen.getByRole("button", { name: "Run again" });
    expect(again).toHaveClass("MuiButton-contained");
    await act(async () => {
      fireEvent.click(again);
    });
    // The server holds a run past the confirm size back for a confirm.
    expect(fakes.startAskRun).toHaveBeenCalledWith(
      expect.objectContaining({ sampleSize: 1000, confirm: false }),
    );
  });

  it("says why a control was refused, inline", async () => {
    fakes.askRuns["task-sessions"] = runOf({
      status: "paused",
      total: 1000,
      answered: 340,
      records: null,
    });
    fakes.control.mockRejectedValue(
      refusal(
        "INSTANT_EVAL_CANCELLED",
        "This run was cancelled. Use Run again to ask these questions again.",
      ),
    );
    renderAt(OPEN);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "This run was cancelled. Use Run again to ask these questions again.",
    );
  });

  it("points a read more or resume refused for the TypeSafe key to AI Providers", async () => {
    fakes.askRuns["task-sessions"] = runOf({
      status: "done",
      total: 1000,
      answered: 1000,
      sample: SAMPLE,
      records: null,
    });
    fakes.control.mockRejectedValue(
      Object.assign(new Error("Request failed with status code 400"), {
        response: {
          status: 400,
          data: { status: false, code: "JEV_KEY_MISSING", result: KEY_MISSING },
        },
      }),
    );
    const { unmount } = renderAt(OPEN);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Read 1,000 more" }));
    });
    expect(fakes.control).toHaveBeenCalledWith("read_more", { more: 1000 });
    let alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(KEY_MISSING.message);
    expect(
      within(alert).getByRole("link", { name: "Open AI Providers" }),
    ).toHaveAttribute("href", AI_PROVIDERS);
    unmount();

    // A resume, as the app's axios hands over the error envelope.
    fakes.askRuns["task-sessions"] = runOf({
      status: "paused",
      total: 1000,
      answered: 340,
      records: null,
    });
    fakes.control.mockRejectedValue({
      status: false,
      code: "JEV_KEY_MISSING",
      message: KEY_MISSING.message,
      statusCode: 400,
      result: KEY_MISSING,
    });
    renderAt(OPEN);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    });
    expect(fakes.control).toHaveBeenCalledWith("resume", {});
    alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(KEY_MISSING.message);
    expect(
      within(alert).getByRole("link", { name: "Open AI Providers" }),
    ).toHaveAttribute("href", AI_PROVIDERS);
  });

  it("reads more only once a run stops answering", async () => {
    fakes.askRuns["task-sessions"] = runOf({
      status: "running",
      total: 1000,
      answered: 300,
      sample: SAMPLE,
      records: null,
    });
    const { unmount } = renderAt(OPEN);
    expect(
      screen.getByRole("button", { name: "Read 1,000 more" }),
    ).toBeDisabled();
    unmount();

    fakes.askRuns["task-sessions"] = runOf({
      status: "done",
      total: 1000,
      answered: 1000,
      sample: SAMPLE,
      records: null,
    });
    fakes.control.mockRejectedValue(
      refusal(
        "INSTANT_EVAL_STILL_ANSWERING",
        "This run is still answering; read more when it finishes.",
      ),
    );
    renderAt(OPEN);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Read 1,000 more" }));
    });
    expect(fakes.control).toHaveBeenCalledWith("read_more", { more: 1000 });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "This run is still answering; read more when it finishes.",
    );
  });
});

describe("a new question's date and filters", () => {
  const STATUS = {
    column_id: "status",
    filter_config: {
      filter_type: "text",
      filter_op: "equals",
      filter_value: "ERROR",
      col_type: "SYSTEM_METRIC",
    },
  };
  const viewOf = (unit, filters, dateOption = "7D") =>
    viewSeed({
      unit,
      filters: [windowItem(presetWindow(dateOption)), ...filters],
      dateFilter: { dateOption },
    });

  it("edits the view's date and filters in place, recounts and starts with them", async () => {
    fakes.startAskRun.mockResolvedValue({
      taskId: "task-4",
      questions: QUESTIONS,
    });
    renderAt(askLink(PROJECT, viewOf("sessions", [STATUS])));
    await screen.findByText(/104,233 sessions in the Sessions view/);
    // Counted as a start would send it: the filters, preset and label.
    expect(fakes.estimate).toHaveBeenLastCalledWith(
      expect.objectContaining({
        unit: "sessions",
        filters: [STATUS, windowItem(presetWindow("7D"))],
        datePreset: "7D",
        label: "Past 7D · 1 filter",
      }),
    );
    // The view's own date and filters, editable here.
    expect(screen.getByText("Status")).toBeInTheDocument();
    expect(screen.queryByText(/Change them in/)).toBeNull();
    expect(
      screen.getByText(
        "Counted with the date range and filters above, started from the Sessions view.",
      ),
    ).toBeInTheDocument();

    fakes.estimate.mockResolvedValue(smallEstimate(40));
    fireEvent.click(screen.getByRole("button", { name: "Past 7D" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Past 30D" }));
    expect(
      await screen.findByText(/40 sessions in the Sessions view/),
    ).toBeInTheDocument();
    const month = [STATUS, windowItem(presetWindow("30D"))];
    expect(fakes.estimate).toHaveBeenLastCalledWith(
      expect.objectContaining({ filters: month }),
    );

    fireEvent.change(screen.getByRole("textbox", { name: "Question 1" }), {
      target: { value: QUESTION },
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Ask Jev about 40 sessions" }),
      );
    });
    expect(fakes.startAskRun).toHaveBeenCalledWith(
      expect.objectContaining({
        filters: month,
        datePreset: "30D",
        label: "Past 30D · 1 filter",
        sampleSize: null,
      }),
    );
    // The run says what it read.
    expect(screen.getByText(/Past 30 days · 1 filter/)).toBeInTheDocument();
  });

  it("counts users a few seconds at a time, and lets a start go ahead", async () => {
    const counts = [
      { ...smallEstimate(209), population_is_lower_bound: true },
      smallEstimate(371),
    ];
    fakes.estimate.mockImplementation(async () =>
      counts.length > 1 ? counts.shift() : counts[0],
    );
    renderAt(
      askLink(PROJECT, {
        ...viewOf("users", [STATUS]),
        search: "acme",
        label: undefined,
      }),
    );
    expect(
      await screen.findByText(/209\+ users in the Users view/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Still counting the users/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Search users" })).toHaveValue(
      "acme",
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Question 1" }), {
      target: { value: QUESTION },
    });
    // It asks about users; their sessions are only an upper bound.
    expect(
      screen.getByRole("button", { name: "Ask Jev about 209 users" }),
    ).toBeEnabled();
    expect(
      await screen.findByText(
        /371 users in the Users view/,
        {},
        { timeout: 4000 },
      ),
    ).toBeInTheDocument();
    expect(fakes.estimate).toHaveBeenLastCalledWith(
      expect.objectContaining({
        unit: "users",
        search: "acme",
        datePreset: "7D",
        label: "Past 7D · 1 filter · “acme”",
      }),
    );
  });

  it("names the users it asks about, not the most sessions it could read", async () => {
    // 12 users, read at up to 5 sessions each: 60 at most, 49 in the view.
    fakes.estimate.mockResolvedValue({
      ...smallEstimate(12),
      sessions_per_user: 5,
      choices: [
        {
          sample_size: null,
          rows_to_read: 60,
          seconds_estimate: 22,
          needs_confirm: false,
          allowed: true,
        },
      ],
    });
    renderAt(askLink(PROJECT, { ...viewOf("users", []), label: undefined }));
    expect(
      await screen.findByText(/Reads up to 60 sessions \(5 per user\)/),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Question 1" }), {
      target: { value: QUESTION },
    });
    expect(
      screen.getByRole("button", { name: "Ask Jev about 12 users" }),
    ).toBeEnabled();
    expect(screen.queryByRole("button", { name: /60 sessions/ })).toBeNull();
  });

  it("keeps the date on another unit and says which filters went", async () => {
    renderAt(`/dashboard/observe/${PROJECT}/ask?new=1&unit=traces&tab=traces`);
    await screen.findByText(/104,233 traces in the Traces view/);
    expect(
      screen.getByText("Counted with the date range and filters above."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Past 7D" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Past 30D" }));
    fireEvent.click(screen.getByRole("button", { name: "Filter" }));
    fireEvent.click(screen.getByRole("button", { name: "Pick status ERROR" }));
    fireEvent.click(screen.getByRole("button", { name: "Close panel" }));
    expect(screen.getByText("Status")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Sessions" }));
    expect(where().params).toMatchObject({ unit: "sessions" });
    expect(
      await screen.findByText(
        "1 filter doesn't apply to sessions, so it was removed. The date range stays.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Past 30D" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Status")).toBeNull();
    await waitFor(() =>
      expect(fakes.estimate).toHaveBeenLastCalledWith(
        expect.objectContaining({
          unit: "sessions",
          filters: [windowItem(presetWindow("30D"))],
        }),
      ),
    );
  });

  it("keeps an edited scope when the unit changes on a seeded page", async () => {
    // The seed is in the URL (its hand-over gone), and switching the unit
    // drops it: the moved scope must still be the one shown.
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?new=1&unit=sessions&tab=sessions&seed=gone`,
    );
    await screen.findByText(/104,233 sessions in the Sessions view/);
    fireEvent.click(screen.getByRole("button", { name: "Past 7D" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Past 30D" }));
    fireEvent.click(screen.getByRole("button", { name: "Traces" }));
    expect(where().params).toMatchObject({ unit: "traces" });
    expect(
      await screen.findByRole("button", { name: "Past 30D" }),
    ).toBeInTheDocument();
  });

  it("keeps the typed questions when the unit changes", async () => {
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?new=1&unit=sessions&tab=sessions`,
    );
    await screen.findByText(/104,233 sessions in the Sessions view/);
    fireEvent.change(screen.getByRole("textbox", { name: "Question 1" }), {
      target: { value: QUESTION },
    });
    fireEvent.click(screen.getByRole("button", { name: "Traces" }));
    await screen.findByText(/104,233 traces in the Traces view/);
    expect(screen.getByRole("textbox", { name: "Question 1" })).toHaveValue(
      QUESTION,
    );
  });

  it("remembers the sample size picked after a turn to every row", async () => {
    fakes.estimate.mockResolvedValue({
      ...ESTIMATE,
      population: 20000,
      choices: [
        ...ESTIMATE.choices.slice(0, 4),
        {
          sample_size: null,
          rows_to_read: 20000,
          seconds_estimate: 4010,
          needs_confirm: true,
          allowed: true,
        },
      ],
    });
    renderAt(askLink(PROJECT, { unit: "sessions" }));
    const select = await screen.findByRole("combobox", { name: /Sample size/ });
    fireEvent.mouseDown(select);
    fireEvent.click(
      await screen.findByRole("option", { name: "2,000 sessions" }),
    );
    expect(
      screen.getByRole("radio", { name: /A sample of 2,000/ }),
    ).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: /All 20,000/ }));
    const sample = screen.getByRole("radio", { name: /A sample of 2,000/ });
    expect(sample).toHaveAttribute("aria-checked", "false");
    fireEvent.click(sample);
    expect(
      screen.getByRole("radio", { name: /A sample of 2,000/ }),
    ).toHaveAttribute("aria-checked", "true");
  });

  it("asks about a selection as it is, or the view's filters instead", async () => {
    fakes.estimate.mockResolvedValue(smallEstimate(3));
    renderAt(
      askLink(PROJECT, {
        ...viewOf("sessions", [STATUS]),
        population: POPULATION,
      }),
    );
    await screen.findByText(/3 selected sessions from Past 7 days · 1 filter/);
    expect(screen.queryByRole("button", { name: "Filter" })).toBeNull();
    fakes.estimate.mockResolvedValue(ESTIMATE);
    fireEvent.click(
      screen.getByRole("button", { name: "Use the view's filters instead" }),
    );
    expect(
      await screen.findByText(/104,233 sessions in the Sessions view/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Filter" })).toBeInTheDocument();
  });
});

describe("a question asked here, once its run is done", () => {
  it("is run again, not recreated, before the saved list is read again", async () => {
    // The question is saved as a new evaluator when it starts; the page's
    // saved list was read before that.
    const NEW = { ...QUESTIONS[0], configId: "config-new" };
    fakes.estimate.mockResolvedValue(smallEstimate(3));
    fakes.startAskRun.mockResolvedValue({
      taskId: "task-new",
      questions: [NEW],
    });
    renderAt(askLink(PROJECT, { unit: "sessions", population: POPULATION }));
    fireEvent.change(
      await screen.findByRole("textbox", { name: "Question 1" }),
      { target: { value: QUESTION } },
    );
    fakes.askRuns["task-new"] = {
      ...runOf(),
      taskId: "task-new",
      questions: [NEW],
    };
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Ask Jev about 3 sessions" }),
      );
    });
    await waitFor(() => expect(where().params.run).toBe("task-new"));
    expect(screen.getByRole("button", { name: "Run again" })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "Recreate question" }),
    ).toBeNull();
  });
});

describe("running a question again", () => {
  const STATUS = {
    column_id: "status",
    filter_config: {
      filter_type: "text",
      filter_op: "equals",
      filter_value: "ERROR",
    },
  };
  const OLD = ["2026-09-01T00:00:00.000Z", "2026-09-08T00:00:00.000Z"];
  const runAsked = (scope) => ({
    ...doneRun,
    summary: {
      ...doneRun.summary,
      scope: {
        filters: [STATUS, windowItem(OLD)],
        sample_size: 1000,
        ids: null,
        ...scope,
      },
    },
  });
  const runAgain = async () => {
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Run again" }));
    });
  };

  beforeEach(() => {
    fakes.startAskRun.mockResolvedValue({
      taskId: "task-5",
      questions: QUESTIONS,
    });
  });

  it("moves a preset's window to now", async () => {
    fakes.askRuns["task-sessions"] = runAsked({
      date_preset: "7D",
      label: "Past 7D · 1 filter",
    });
    await runAgain();
    expect(fakes.startAskRun).toHaveBeenCalledWith(
      expect.objectContaining({
        filters: [STATUS, windowItem(presetWindow("7D"))],
        datePreset: "7D",
        label: "Past 7D · 1 filter",
        sampleSize: 1000,
        confirm: false,
      }),
    );
  });

  it("reads a custom range again as it was", async () => {
    fakes.askRuns["task-sessions"] = runAsked({
      date_preset: "Custom",
      label: "Custom range · 1 filter",
    });
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    // The header names a custom range by its dates.
    expect(screen.getByText(/Oct 13 – Oct 14 · 1 filter/)).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Run again" }));
    });
    expect(fakes.startAskRun).toHaveBeenCalledWith(
      expect.objectContaining({
        filters: [STATUS, windowItem(OLD)],
        datePreset: "Custom",
        label: "Custom range · 1 filter",
      }),
    );
  });
});

describe("a sample size typed in", () => {
  // The server costs any size as it costs the fixed ones.
  const costed =
    (estimate) =>
    async ({ sampleSize }) =>
      sampleSize
        ? {
            ...estimate,
            rows_to_read: Math.min(sampleSize, estimate.population),
            // Slower than reading every row would suggest, to tell them apart.
            seconds_estimate: 10 + sampleSize,
            needs_confirm: sampleSize > estimate.confirm_rows,
          }
        : estimate;
  const sizesAsked = () =>
    fakes.estimate.mock.calls
      .map(([args]) => args.sampleSize)
      .filter((size) => size != null);
  const pickCustom = async () => {
    const select = await screen.findByRole("combobox", { name: /Sample size/ });
    fireEvent.mouseDown(select);
    fireEvent.click(await screen.findByRole("option", { name: "Custom…" }));
    return screen.getByRole("spinbutton", { name: "Custom sample size" });
  };
  const ask = () =>
    fireEvent.change(screen.getByRole("textbox", { name: "Question 1" }), {
      target: { value: QUESTION },
    });

  it("costs a typed size on the server and starts with it", async () => {
    fakes.estimate.mockImplementation(costed(ESTIMATE));
    fakes.startAskRun.mockResolvedValue({
      taskId: "task-6",
      questions: QUESTIONS,
    });
    renderAt(askLink(PROJECT, { unit: "sessions" }));
    await screen.findByText(/104,233 sessions in the Sessions view/);
    const box = await pickCustom();
    // It starts from the size that was shown.
    expect(box).toHaveValue(1000);
    fireEvent.change(box, { target: { value: "1500" } });
    const card = screen.getByRole("radio", { name: /A sample of/ });
    expect(card).toHaveAttribute("aria-checked", "true");
    // Costed by the server once the typing settles.
    expect(
      await within(card).findByText(
        /Reads 1,500 sessions · about 90 credits · about 25 min/,
        {},
        { timeout: 4000 },
      ),
    ).toBeInTheDocument();
    expect(sizesAsked()).toEqual([1500]);
    ask();
    const start = await screen.findByRole("button", {
      name: "Ask Jev about a sample of 1,500 sessions",
    });
    await waitFor(() => expect(start).toBeEnabled(), { timeout: 4000 });
    await act(async () => {
      fireEvent.click(start);
    });
    expect(fakes.startAskRun).toHaveBeenCalledWith(
      expect.objectContaining({ sampleSize: 1500, confirm: false }),
    );
  });

  it("says plainly why a size can't be read", async () => {
    const big = { ...ESTIMATE, population: 150000 };
    fakes.estimate.mockImplementation(costed(big));
    renderAt(askLink(PROJECT, { unit: "sessions" }));
    await screen.findByText(/150,000 sessions in the Sessions view/);
    const box = await pickCustom();
    ask();
    for (const [typed, why] of [
      ["0", "Choose a sample of at least 1."],
      ["120000", "One run reads at most 100,000 rows; choose up to 100,000."],
    ]) {
      fireEvent.change(box, { target: { value: typed } });
      expect(screen.getByText(why)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Ask Jev" })).toBeDisabled();
    }
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 400));
    });
    // None of them is costed.
    expect(sizesAsked()).toEqual([]);
    // The largest that can run is one click away.
    fireEvent.click(screen.getByRole("button", { name: "Use 100,000" }));
    expect(box).toHaveValue(100000);
  });

  it("offers the most a smaller view holds", async () => {
    const small = { ...ESTIMATE, population: 3744 };
    fakes.estimate.mockImplementation(costed(small));
    renderAt(askLink(PROJECT, { unit: "sessions" }));
    await screen.findByText(/3,744 sessions in the Sessions view/);
    const box = await pickCustom();
    fireEvent.change(box, { target: { value: "9000" } });
    expect(
      screen.getByText(
        "There are only 3,744 sessions in this view; choose up to 3,744.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use 3,744" }));
    expect(box).toHaveValue(3744);
  });

  it("says inline why a start was refused for its size", async () => {
    const { AskSampleTooLarge } = await import("../../useInstantEval");
    fakes.estimate.mockImplementation(costed(ESTIMATE));
    fakes.startAskRun.mockRejectedValue(
      new AskSampleTooLarge({
        message:
          "There are only 1,400 sessions in this view; choose up to 1,400.",
        sample_size: 1400,
      }),
    );
    renderAt(askLink(PROJECT, { unit: "sessions" }));
    await screen.findByText(/104,233 sessions in the Sessions view/);
    fireEvent.change(await pickCustom(), { target: { value: "1500" } });
    ask();
    const start = await screen.findByRole("button", {
      name: "Ask Jev about a sample of 1,500 sessions",
    });
    await waitFor(() => expect(start).toBeEnabled(), { timeout: 4000 });
    await act(async () => {
      fireEvent.click(start);
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "There are only 1,400 sessions in this view; choose up to 1,400.",
    );
  });

  it("turns a size the view no longer holds into every row, and says so", async () => {
    fakes.estimate.mockImplementation(costed(ESTIMATE));
    renderAt(askLink(PROJECT, { unit: "sessions" }));
    await screen.findByText(/104,233 sessions in the Sessions view/);
    fireEvent.change(await pickCustom(), { target: { value: "1500" } });
    // The view shrinks below the sample: a filter, a shorter date.
    fakes.estimate.mockImplementation(costed(smallEstimate(400)));
    fireEvent.click(screen.getByRole("button", { name: "Past 7D" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Today" }));
    expect(
      await screen.findByText(
        "This view now holds 400 sessions, fewer than the sample of 1,500, so Jev reads every one.",
        {},
        { timeout: 4000 },
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("radio", { name: /Every one of the 400/ }),
    ).toHaveAttribute("aria-checked", "true");
  });

  it("shows the server's reason when it refuses a size it costed", async () => {
    fakes.estimate.mockImplementation(async ({ sampleSize }) =>
      sampleSize
        ? {
            ...ESTIMATE,
            population: 1200,
            sample_error: {
              error_code: "INSTANT_EVAL_SAMPLE_TOO_LARGE",
              message:
                "There are only 1,200 sessions in this view; choose up to 1,200.",
              sample_size: 1200,
            },
          }
        : ESTIMATE,
    );
    renderAt(askLink(PROJECT, { unit: "sessions" }));
    await screen.findByText(/104,233 sessions in the Sessions view/);
    fireEvent.change(await pickCustom(), { target: { value: "1500" } });
    expect(
      await screen.findByText(
        "There are only 1,200 sessions in this view; choose up to 1,200.",
        {},
        { timeout: 4000 },
      ),
    ).toBeInTheDocument();
    ask();
    expect(screen.getByRole("button", { name: "Ask Jev" })).toBeDisabled();
  });

  it("waits for the typing to settle before costing it", async () => {
    fakes.estimate.mockImplementation(costed(ESTIMATE));
    renderAt(askLink(PROJECT, { unit: "sessions" }));
    await screen.findByText(/104,233 sessions in the Sessions view/);
    const box = await pickCustom();
    for (const typed of ["1", "12", "120", "1200"])
      fireEvent.change(box, { target: { value: typed } });
    await waitFor(() => expect(sizesAsked()).toEqual([1200]), {
      timeout: 4000,
    });
  });

  it("costs a typed size from every row when the server can't", async () => {
    fakes.estimate.mockImplementation(async ({ sampleSize }) => {
      if (sampleSize) throw new Error("unavailable");
      return ESTIMATE;
    });
    renderAt(askLink(PROJECT, { unit: "sessions" }));
    await screen.findByText(/104,233 sessions in the Sessions view/);
    fireEvent.change(await pickCustom(), { target: { value: "1500" } });
    ask();
    const start = await screen.findByRole("button", {
      name: "Ask Jev about a sample of 1,500 sessions",
    });
    await waitFor(() => expect(start).toBeEnabled(), { timeout: 4000 });
    expect(
      within(screen.getByRole("radio", { name: /A sample of/ })).getByText(
        /Reads 1,500 sessions/,
      ),
    ).toBeInTheDocument();
  });

  it("restates the credits for a typed size past the confirm limit", async () => {
    fakes.estimate.mockImplementation(costed(ESTIMATE));
    fakes.startAskRun.mockResolvedValue({
      taskId: "task-7",
      questions: QUESTIONS,
    });
    renderAt(askLink(PROJECT, { unit: "sessions" }));
    await screen.findByText(/104,233 sessions in the Sessions view/);
    fireEvent.change(await pickCustom(), { target: { value: "6000" } });
    ask();
    const start = await screen.findByRole("button", {
      name: "Ask Jev about a sample of 6,000 sessions",
    });
    await waitFor(() => expect(start).toBeEnabled(), { timeout: 4000 });
    fireEvent.click(start);
    expect(fakes.startAskRun).not.toHaveBeenCalled();
    expect(
      screen.getByText(/Read 6,000 sessions\? That is about 360 credits/),
    ).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Start" }));
    });
    expect(fakes.startAskRun).toHaveBeenCalledWith(
      expect.objectContaining({ sampleSize: 6000, confirm: true }),
    );
  });

  it("remembers a typed size after a turn to every row", async () => {
    const view = {
      ...ESTIMATE,
      population: 20000,
      choices: [
        ...ESTIMATE.choices.slice(0, 4),
        {
          sample_size: null,
          rows_to_read: 20000,
          seconds_estimate: 4010,
          needs_confirm: true,
          allowed: true,
        },
      ],
    };
    fakes.estimate.mockImplementation(costed(view));
    renderAt(askLink(PROJECT, { unit: "sessions" }));
    fireEvent.change(await pickCustom(), { target: { value: "1500" } });
    fireEvent.click(screen.getByRole("radio", { name: /All 20,000/ }));
    const sample = screen.getByRole("radio", { name: /A sample of/ });
    expect(sample).toHaveAttribute("aria-checked", "false");
    expect(
      screen.getByRole("spinbutton", { name: "Custom sample size" }),
    ).toHaveValue(1500);
    fireEvent.click(sample);
    expect(sample).toHaveAttribute("aria-checked", "true");
    expect(
      await within(sample).findByText(/Reads 1,500 sessions/),
    ).toBeInTheDocument();
  });

  describe("on a view too small for the fixed sizes", () => {
    it("reads every row by default, or a sample typed in", async () => {
      fakes.estimate.mockImplementation(costed(smallEstimate(108)));
      fakes.startAskRun.mockResolvedValue({
        taskId: "task-8",
        questions: QUESTIONS,
      });
      renderAt(askLink(PROJECT, { unit: "sessions" }));
      const all = await screen.findByRole("radio", {
        name: /Every one of the 108/,
      });
      expect(all).toHaveAttribute("aria-checked", "true");
      expect(
        screen.queryByRole("combobox", { name: /Sample size/ }),
      ).toBeNull();
      fireEvent.click(
        screen.getByRole("button", { name: "Read a sample instead" }),
      );
      const box = screen.getByRole("spinbutton", {
        name: "Custom sample size",
      });
      fireEvent.change(box, { target: { value: "20" } });
      expect(all).toHaveAttribute("aria-checked", "false");
      expect(
        await screen.findByText(
          /Reads 20 sessions · about 1\.2 credits · about 30 s/,
          {},
          { timeout: 4000 },
        ),
      ).toBeInTheDocument();
      expect(sizesAsked()).toEqual([20]);
      ask();
      const start = await screen.findByRole("button", {
        name: "Ask Jev about a sample of 20 sessions",
      });
      await waitFor(() => expect(start).toBeEnabled(), { timeout: 4000 });
      await act(async () => {
        fireEvent.click(start);
      });
      expect(fakes.startAskRun).toHaveBeenCalledWith(
        expect.objectContaining({ sampleSize: 20, confirm: false }),
      );
    });

    it("says a budget pause beside the sample chosen, not under every row", async () => {
      // Cloud, a Pause budget tripped: billing stops every choice.
      const PAUSED =
        "Jev runs are paused by your budget limit. Raise or remove it in billing to run this.";
      fakes.estimate.mockImplementation(
        costed({
          ...smallEstimate(49),
          credit_check: {
            allowed: false,
            error_code: "BUDGET_PAUSED",
            credits_left: null,
            blocked: true,
            message: PAUSED,
          },
        }),
      );
      renderAt(askLink(PROJECT, { unit: "sessions" }));
      const all = await screen.findByRole("radio", {
        name: /Every one of the 49/,
      });
      // Every row is the choice: its card says why it can't run.
      expect(all.parentElement).toHaveTextContent(PAUSED);
      fireEvent.click(
        screen.getByRole("button", { name: "Read a sample instead" }),
      );
      fireEvent.change(
        screen.getByRole("spinbutton", { name: "Custom sample size" }),
        { target: { value: "5" } },
      );
      await screen.findByText(/Reads 5 sessions/, {}, { timeout: 4000 });
      // The sample is the choice now: the pause is said there, once.
      expect(screen.getAllByText(PAUSED)).toHaveLength(1);
      expect(all.parentElement).not.toHaveTextContent(PAUSED);
    });

    it("reads every row for a sample as big as the view", async () => {
      fakes.estimate.mockImplementation(costed(smallEstimate(108)));
      fakes.startAskRun.mockResolvedValue({
        taskId: "task-9",
        questions: QUESTIONS,
      });
      renderAt(askLink(PROJECT, { unit: "sessions" }));
      await screen.findByRole("radio", { name: /Every one of the 108/ });
      fireEvent.click(
        screen.getByRole("button", { name: "Read a sample instead" }),
      );
      const box = screen.getByRole("spinbutton", {
        name: "Custom sample size",
      });
      for (const typed of ["108", "500"]) {
        fireEvent.change(box, { target: { value: typed } });
        expect(
          screen.getByText(
            "That is every one of the 108, so Jev reads every one.",
          ),
        ).toBeInTheDocument();
        expect(
          screen.getByRole("radio", { name: /Every one of the 108/ }),
        ).toHaveAttribute("aria-checked", "true");
      }
      ask();
      await act(async () => {
        fireEvent.click(
          screen.getByRole("button", { name: "Ask Jev about 108 sessions" }),
        );
      });
      expect(fakes.startAskRun).toHaveBeenCalledWith(
        expect.objectContaining({ sampleSize: null }),
      );
    });
  });
});

describe("a run that failed before reading a row", () => {
  it("says so in red and offers Retry", async () => {
    const run = runOf({ status: "failed", total: 0, answered: 0, records: [] });
    run.summary.counts = {};
    fakes.askRuns["task-sessions"] = run;
    fakes.startAskRun.mockResolvedValue({
      taskId: "task-again",
      questions: QUESTIONS,
    });
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    const status = screen.getByText("Failed before answering");
    expect(status).toBeInTheDocument();
    expect(screen.queryByText(/answered in/)).toBeNull();
    // Nothing was read, so there is no sample to read more of.
    expect(screen.queryByRole("button", { name: /^Read all/ })).toBeNull();
    const again = screen.getByRole("button", { name: "Retry" });
    expect(again).toHaveClass("MuiButton-contained");
    await act(async () => {
      fireEvent.click(again);
    });
    expect(fakes.startAskRun).toHaveBeenCalledTimes(1);
  });
});

describe("recreating a deleted evaluator", () => {
  afterEach(() => {
    axios.get.mockRestore();
    axios.post.mockRestore();
  });

  it.each(["run", "history", "category run", "open composer"])(
    "starts with a new evaluator id after recreating from %s",
    async (source) => {
      fakes.saved = [];
      fakes.estimate.mockResolvedValue(smallEstimate(3));
      const run = runOf({
        status: "failed",
        total: 3,
        answered: 0,
        failed: 3,
        records: [],
      });
      run.summary.counts = { [CONFIG]: { __error__: 3 } };
      if (source === "category run") {
        run.questions = [
          {
            ...QUESTIONS[0],
            answerType: "category",
            labels: ["Resolved", "Unresolved"],
          },
        ];
      }
      fakes.askRuns["task-sessions"] = run;
      const fromHistory = source === "history" || source === "open composer";
      if (fromHistory) {
        globalThis.localStorage.setItem("ask-rail-collapsed", "0");
        const { historyRun } = await vi.importActual("../../useInstantEval");
        fakes.runs = [
          historyRun({
            task_id: run.taskId,
            status: "failed",
            unit: "sessions",
            rows: 3,
            answered: 3,
            config_ids: [CONFIG],
            questions: [QUESTION],
            counts: run.summary.counts,
          }),
        ];
      }
      const actual = await vi.importActual("../../useInstantEval");
      fakes.startAskRun.mockImplementation(actual.startAskRun);
      vi.spyOn(axios, "get").mockResolvedValue({ data: { result: [] } });
      const post = vi
        .spyOn(axios, "post")
        .mockImplementation(async (url, body) => {
          if (url === endpoints.develop.eval.createEvalTemplateV2) {
            if (source === "category run")
              expect(body.choice_scores).toEqual({
                Resolved: 1,
                Unresolved: 1,
              });
            return { data: { result: { id: "replacement-template" } } };
          }
          if (url === endpoints.project.createEvalTaskConfig())
            return { data: { result: { id: "replacement-config" } } };
          expect(url).toBe(endpoints.project.instantEvalStart());
          expect(body.config_ids).toEqual(["replacement-config"]);
          return {
            data: { result: { task_id: "task-recovered", rows_requested: 3 } },
          };
        });
      renderAt(
        `/dashboard/observe/${PROJECT}/ask?${source === "open composer" ? "new=1" : "run=task-sessions"}&unit=sessions&tab=sessions`,
      );
      if (source === "open composer") {
        fireEvent.change(
          await screen.findByRole("textbox", { name: "Question 1" }),
          {
            target: { value: "A different question?" },
          },
        );
      }
      const region = fromHistory
        ? screen.getByRole("navigation", { name: "Past questions" })
        : document.body;
      expect(
        within(region).queryByRole("button", { name: "Retry" }),
      ).toBeNull();
      fireEvent.click(
        within(region).getByRole("button", { name: "Recreate question" }),
      );
      expect(
        await screen.findByRole("textbox", { name: "Question 1" }),
      ).toHaveValue(QUESTION);
      expect(
        screen.getByText(/Review the question, labels, inputs and date range/),
      ).toBeInTheDocument();
      fireEvent.click(
        await screen.findByRole("button", { name: "Ask Jev about 3 sessions" }),
      );
      await waitFor(() => expect(where().params.run).toBe("task-recovered"));
      expect(
        post.mock.calls.find(
          ([url]) => url === endpoints.project.instantEvalStart(),
        )[1].config_ids,
      ).toEqual(["replacement-config"]);
    },
  );
});

describe("a run that failed partway", () => {
  it("says how far it got in red, offers Run again and keeps its sample", async () => {
    fakes.askRuns["task-sessions"] = runOf({
      status: "failed",
      total: 10,
      answered: 4,
      sample: {
        is_sample: true,
        population: 1000,
        population_is_lower_bound: false,
        requested: 10,
        read: 4,
        next_size: 10,
        can_extend: false,
      },
      records: null,
    });
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(
      screen.getByText("This run failed: 4 of 10 answered. Run it again."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run again" })).toHaveClass(
      "MuiButton-contained",
    );
    // The rows it read still estimate the view.
    expect(
      screen.getByText(/^4 of 1,000 sessions read, picked at random/),
    ).toBeInTheDocument();
  });
});

describe("running a question again past the confirm size", () => {
  const OPEN = `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`;
  const held = async (credits) => {
    const { AskNeedsConfirm } = await import("../../useInstantEval");
    fakes.askRuns["task-sessions"] = runOf({ total: 6000, answered: 6000 });
    fakes.startAskRun
      .mockRejectedValueOnce(
        new AskNeedsConfirm({
          message: "Confirm to start.",
          rows_to_read: 6000,
          credits_estimate: credits,
        }),
      )
      .mockResolvedValue({ taskId: "task-again", questions: QUESTIONS });
    renderAt(OPEN);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Run again" }));
    });
  };

  it("restates the credits inline and starts only once confirmed", async () => {
    await held(360);
    expect(fakes.startAskRun).toHaveBeenCalledTimes(1);
    expect(fakes.startAskRun).toHaveBeenLastCalledWith(
      expect.objectContaining({ confirm: false }),
    );
    expect(
      screen.getByText(
        /Read 6,000 sessions again\? That is about 360 credits\./,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run again" })).toBeDisabled();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Start" }));
    });
    expect(fakes.startAskRun).toHaveBeenCalledTimes(2);
    expect(fakes.startAskRun).toHaveBeenLastCalledWith(
      expect.objectContaining({ confirm: true, sampleSize: null }),
    );
    expect(where().params.run).toBe("task-again");
  });

  it("goes back without starting, and names no credits where none are billed", async () => {
    await held(null);
    const confirm = screen.getByText(/Read 6,000 sessions again\?/);
    expect(confirm).toHaveTextContent(
      "Read 6,000 sessions again? You can pause or cancel it while it runs.",
    );
    expect(confirm).not.toHaveTextContent(/credit/);
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.queryByText(/Read 6,000 sessions again/)).toBeNull();
    expect(fakes.startAskRun).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Run again" })).toBeEnabled();
  });
});

describe("handing a selection over", () => {
  const OPEN = (unit) =>
    `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=${unit}&tab=${unit}`;
  const idsOf = (count) =>
    Array.from({ length: count }, (_, n) => ({
      id: `s${n}`,
      time: "2026-10-13T14:10:00Z",
      user_id: "",
      trace_id: `t${n}`,
    }));
  const bar = () => screen.getByRole("region", { name: "Selected rows" });

  beforeEach(() => {
    fetchRowIds.mockReset();
  });

  it("refuses Find patterns past 5,000 rows, rather than the whole view", async () => {
    fakes.askRuns["task-sessions"] = runOf();
    fakes.rowsTotal = 6000;
    renderAt(OPEN("sessions"));
    fireEvent.click(screen.getByRole("button", { name: "Focus No" }));
    await act(async () => {
      fireEvent.click(
        within(bar()).getByRole("button", { name: /Find patterns/ }),
      );
    });
    expect(within(bar()).getByRole("alert")).toHaveTextContent(
      "Narrow the selection to 5,000 sessions or fewer to find patterns.",
    );
    expect(fetchRowIds).not.toHaveBeenCalled();
    expect(where().path).toBe(`/dashboard/observe/${PROJECT}/ask`);
    // Another selection starts afresh.
    fireEvent.click(screen.getByRole("button", { name: "Select the outage" }));
    expect(within(bar()).queryByRole("alert")).toBeNull();
  });

  it("refuses when the rows read turn out to be more than the count said", async () => {
    fakes.askRuns["task-sessions"] = runOf();
    fakes.rowsTotal = 4000;
    fetchRowIds.mockResolvedValue(idsOf(5001));
    renderAt(OPEN("sessions"));
    fireEvent.click(screen.getByRole("button", { name: "Focus No" }));
    await act(async () => {
      fireEvent.click(
        within(bar()).getByRole("button", { name: /Find patterns/ }),
      );
    });
    expect(fetchRowIds).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 5001 }),
    );
    expect(within(bar()).getByRole("alert")).toHaveTextContent(
      /Narrow the selection to 5,000 sessions/,
    );
    expect(where().path).toBe(`/dashboard/observe/${PROJECT}/ask`);
  });

  it("hands Insights exactly the selected rows by id", async () => {
    fakes.askRuns["task-sessions"] = runOf();
    fetchRowIds.mockResolvedValue(idsOf(2));
    renderAt(OPEN("sessions"));
    fireEvent.click(screen.getByRole("button", { name: "Focus No" }));
    await act(async () => {
      fireEvent.click(
        within(bar()).getByRole("button", { name: /Find patterns/ }),
      );
    });
    expect(where().path).toBe(`/dashboard/observe/${PROJECT}/insights`);
    const seed = readHandoff(where().params.seed);
    expect(seed.population.map((item) => item.id)).toEqual(["s0", "s1"]);
    expect(seed).not.toHaveProperty("filters");
    expect(seed.label).toBe(`No · ${QUESTION}`);
  });

  it("refuses Show in Traces past what one link names, never cutting it short", async () => {
    fakes.askRuns["task-sessions"] = runOf();
    fakes.rowsTotal = 300;
    renderAt(OPEN("sessions"));
    fireEvent.click(screen.getByRole("button", { name: "Focus No" }));
    await act(async () => {
      fireEvent.click(
        within(bar()).getByRole("button", { name: "Show in Traces" }),
      );
    });
    expect(within(bar()).getByRole("alert")).toHaveTextContent(
      "Narrow the selection to 200 sessions or fewer to show it in Traces.",
    );
    expect(where().path).toBe(`/dashboard/observe/${PROJECT}/ask`);
  });

  it("shows up to 200 rows in Traces by id", async () => {
    fakes.askRuns["task-sessions"] = runOf();
    fetchRowIds.mockResolvedValue(idsOf(2));
    renderAt(OPEN("sessions"));
    fireEvent.click(screen.getByRole("button", { name: "Focus No" }));
    await act(async () => {
      fireEvent.click(
        within(bar()).getByRole("button", { name: "Show in Traces" }),
      );
    });
    expect(fetchRowIds).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 201 }),
    );
    expect(where().path).toBe(`/dashboard/observe/${PROJECT}/llm-tracing`);
    const [filter] = JSON.parse(where().params.observeLinkFilter);
    expect(filter).toMatchObject({
      column_id: "session_id",
      filter_config: { filter_value: ["s0", "s1"] },
    });
  });

  it("shows a trace run's focused answer in Traces by id, up to 200", async () => {
    fakes.askRuns["task-sessions"] = {
      ...runOf(),
      unit: "traces",
      knownUnit: "traces",
    };
    fetchRowIds.mockResolvedValue(idsOf(2));
    renderAt(OPEN("traces"));
    fireEvent.click(screen.getByRole("button", { name: "Focus No" }));
    expect(within(bar()).queryByText(/every trace this evaluator/)).toBeNull();
    // One way to Traces: the bar's, which names the rows first.
    expect(screen.queryByRole("link", { name: "Show in Traces" })).toBeNull();
    expect(
      screen.getAllByRole("button", { name: "Show in Traces" }),
    ).toHaveLength(1);
    await act(async () => {
      fireEvent.click(
        within(bar()).getByRole("button", { name: "Show in Traces" }),
      );
    });
    expect(fetchRowIds).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 201 }),
    );
    expect(where().path).toBe(`/dashboard/observe/${PROJECT}/llm-tracing`);
    const [filter] = JSON.parse(where().params.observeLinkFilter);
    expect(filter).toMatchObject({
      column_id: "trace_id",
      filter_config: { filter_value: ["s0", "s1"] },
    });
  });

  it("falls back to the answer filter when the rows read pass 200", async () => {
    fakes.askRuns["task-sessions"] = {
      ...runOf(),
      unit: "traces",
      knownUnit: "traces",
    };
    // The count said 150; the read found more.
    fakes.rowsTotal = 150;
    fetchRowIds.mockResolvedValue(idsOf(201));
    renderAt(OPEN("traces"));
    fireEvent.click(screen.getByRole("button", { name: "Focus No" }));
    await act(async () => {
      fireEvent.click(
        within(bar()).getByRole("button", { name: "Show in Traces" }),
      );
    });
    const [filter] = JSON.parse(where().params.observeLinkFilter);
    expect(filter).toMatchObject({
      column_id: CONFIG,
      filter_config: { filter_value: ["No"], col_type: "EVAL_METRIC" },
    });
  });

  it("past 200, shows a trace run's focused answer in Traces as a filter, and says what it lists", async () => {
    fakes.askRuns["task-sessions"] = {
      ...runOf(),
      unit: "traces",
      knownUnit: "traces",
    };
    fakes.rowsTotal = 60000;
    renderAt(OPEN("traces"));
    fireEvent.click(screen.getByRole("button", { name: "Focus No" }));
    expect(
      within(bar()).getByText(
        "Over 200 traces, Show in Traces filters on this answer: it lists every trace this evaluator answered that way in the window, not only this run's.",
      ),
    ).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(
        within(bar()).getByRole("button", { name: "Show in Traces" }),
      );
    });
    expect(fetchRowIds).not.toHaveBeenCalled();
    expect(where().path).toBe(`/dashboard/observe/${PROJECT}/llm-tracing`);
    const [filter] = JSON.parse(where().params.observeLinkFilter);
    expect(filter).toMatchObject({
      column_id: CONFIG,
      filter_config: { filter_value: ["No"], col_type: "EVAL_METRIC" },
    });
  });
});

describe("Keep answering", () => {
  const STATUS = {
    column_id: "status",
    filter_config: {
      filter_type: "text",
      filter_op: "equals",
      filter_value: "ERROR",
    },
  };
  const traceRun = (scope, extra = {}) => {
    const run = runOf();
    return {
      ...run,
      unit: "traces",
      knownUnit: "traces",
      creditsUsed: 0.18,
      ...extra,
      summary: {
        ...run.summary,
        scope: { ...run.summary.scope, ...scope },
      },
    };
  };
  const OPEN = `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=traces&tab=traces`;

  it("is offered for a run opened by its link, with the filters the server kept", () => {
    fakes.askRuns["task-sessions"] = traceRun({ filters: [STATUS] });
    renderAt(OPEN);
    expect(screen.getByText("Keep answering new traces")).toBeInTheDocument();
    expect(fakes.keep.at(-1)).toMatchObject({
      unit: "traces",
      billed: true,
      run: { items: QUESTIONS, scopeFilters: [STATUS] },
    });
  });

  it("isn't offered for rows handed over by id, which have no view to keep to", () => {
    fakes.askRuns["task-sessions"] = traceRun({
      filters: [],
      ids: ["t1", "t2"],
    });
    renderAt(OPEN);
    expect(screen.queryByText("Keep answering new traces")).toBeNull();
  });

  it("says nothing of billing where answers aren't billed", () => {
    fakes.askRuns["task-sessions"] = traceRun(
      { filters: [STATUS] },
      { creditsUsed: null },
    );
    renderAt(OPEN);
    expect(fakes.keep.at(-1).billed).toBe(false);
  });
});

describe("copying a run's link", () => {
  const OPEN = `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`;
  const original = Object.getOwnPropertyDescriptor(
    globalThis.navigator,
    "clipboard",
  );
  const withClipboard = (clipboard) =>
    Object.defineProperty(globalThis.navigator, "clipboard", {
      value: clipboard,
      configurable: true,
    });
  const copy = async () => {
    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    await act(async () => {
      fireEvent.click(
        screen.getByRole("menuitem", { name: "Copy link to this run" }),
      );
    });
  };

  afterEach(() => {
    if (original)
      Object.defineProperty(globalThis.navigator, "clipboard", original);
    else delete globalThis.navigator.clipboard;
  });

  it("says the link was copied once the clipboard has it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    withClipboard({ writeText });
    fakes.askRuns["task-sessions"] = doneRun;
    renderAt(OPEN);
    await copy();
    expect(writeText).toHaveBeenCalledWith(globalThis.location.href);
    expect(
      screen.getByText("Link copied. It opens this run with its answers."),
    ).toBeInTheDocument();
  });

  it.each([
    ["there is no clipboard (plain http)", undefined],
    [
      "the browser refuses",
      { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    ],
  ])("points to the address bar when %s", async (_, clipboard) => {
    withClipboard(clipboard);
    fakes.askRuns["task-sessions"] = doneRun;
    renderAt(OPEN);
    await copy();
    expect(screen.queryByText(/Link copied/)).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The link couldn't be copied here. Copy it from the address bar.",
    );
  });

  it("clears an earlier failure once a copy works", async () => {
    withClipboard(undefined);
    fakes.askRuns["task-sessions"] = doneRun;
    renderAt(OPEN);
    await copy();
    expect(screen.getByText(/The link couldn't be copied here/)).toBeVisible();

    withClipboard({ writeText: vi.fn().mockResolvedValue(undefined) });
    await copy();
    expect(
      screen.getByText("Link copied. It opens this run with its answers."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/The link couldn't be copied here/)).toBeNull();
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });
});

describe("where answers aren't billed", () => {
  // Self-hosted, or no billing set up: the server sends no credit figures.
  const UNBILLED = {
    ...ESTIMATE,
    credits_estimate: null,
    credits_per_answer: null,
    credit_check: {
      allowed: true,
      error_code: null,
      current_usage: null,
      limit: null,
      credits_left: null,
      blocked: false,
      message: null,
    },
    credit_max_sample: null,
    choices: ESTIMATE.choices.map((choice) => ({
      ...choice,
      credits_estimate: null,
    })),
  };
  const noCredits = () =>
    expect(screen.queryAllByText(/credit/i)).toHaveLength(0);

  it("costs a question in rows and time only, and confirms without credits", async () => {
    fakes.estimate.mockResolvedValue({
      ...UNBILLED,
      population: 20000,
      choices: [
        ...UNBILLED.choices.slice(0, 4),
        {
          sample_size: null,
          rows_to_read: 20000,
          seconds_estimate: 4010,
          needs_confirm: true,
          allowed: true,
          credits_estimate: null,
        },
      ],
    });
    fakes.startAskRun.mockResolvedValue({
      taskId: "task-2",
      questions: QUESTIONS,
    });
    renderAt(askLink(PROJECT, { unit: "sessions", filters: [] }));
    const sample = await screen.findByRole("radio", {
      name: /A sample of 1,000/,
    });
    expect(
      within(sample).getByText("Reads 1,000 sessions · about 4 min", {
        exact: false,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("You confirm before it starts.")).toBeVisible();
    expect(screen.getByText(/TypeSafe bills its tokens/)).not.toHaveTextContent(
      /billed as one eval run/,
    );
    noCredits();

    fireEvent.click(screen.getByRole("radio", { name: /All 20,000/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "Question 1" }), {
      target: { value: QUESTION },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Ask Jev about 20,000 sessions" }),
    );
    expect(
      screen.getByText(
        /Read 20,000 sessions for 1 question\? It takes about 1 h 7 min\./,
      ),
    ).toBeInTheDocument();
    noCredits();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Start" }));
    });
    expect(fakes.startAskRun).toHaveBeenCalledWith(
      expect.objectContaining({ sampleSize: null, confirm: true }),
    );
  });

  it("shows a run, and a read-more confirm, without credits", async () => {
    const sample = {
      population: 104233,
      population_is_lower_bound: false,
      requested: 1000,
      read: 1000,
      is_sample: true,
      can_extend: true,
      next_size: 6000,
    };
    const run = runOf({ total: 1000, answered: 1000, sample, records: null });
    fakes.askRuns["task-sessions"] = {
      ...run,
      creditsUsed: null,
      summary: { ...run.summary, credits_estimate: null },
    };
    fakes.control.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          result: {
            message: "Reading 6,000 more rows. Confirm to start.",
            error_code: "INSTANT_EVAL_CONFIRM",
            rows_to_read: 6000,
            credits_estimate: null,
          },
        },
      },
    });
    renderAt(
      `/dashboard/observe/${PROJECT}/ask?run=task-sessions&unit=sessions&tab=sessions`,
    );
    expect(screen.getByText(/^Started by Nikhil/)).not.toHaveTextContent(
      /credit/,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Read 6,000 more" }));
    });
    expect(screen.getByText("Read 6,000 more sessions?")).toBeInTheDocument();
    noCredits();
  });
});

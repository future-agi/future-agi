import React from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import { statusColors } from "src/utils/runStatus";
import { readHandoff } from "src/sections/projects/observeHandoff";

const http = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: http.get },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureDenied: () => false,
}));
vi.mock("../../results/KeepAnswering", () => ({ default: () => null }));

import AskRunView from "../AskRunView";
import { ROWS_PAGE } from "../useAskRows";

const CONFIG = "config-1";
const QUESTION = {
  configId: CONFIG,
  templateId: "template-1",
  question: "Was the issue resolved?",
  answerType: "yes_no",
  labels: ["Yes", "No"],
};
const theme = createTheme({ palette: palette("light") });
const row = (i, answer = "Yes") => ({
  id: `s${i}`,
  time: "2026-10-13T14:00:00Z",
  label: `Session ${i}`,
  excerpt: `Customer question ${i}`,
  user_id: `user-${i}`,
  facets: {},
  answers: { [CONFIG]: { answer, confidence: 0.9, status: "completed" } },
});
const response = (rows, total, next = null) => ({
  data: { result: { rows, total, next_cursor: next } },
});
const runOf = (extra = {}) => ({
  taskId: "task-1",
  status: "running",
  questions: [QUESTION],
  records: null,
  summary: {
    total: 53,
    counts: { [CONFIG]: { Yes: 50, No: 3 } },
    unsure: {},
    groups: {},
  },
  unit: "sessions",
  tz: "America/New_York",
  window: ["2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z"],
  total: 53,
  answered: 53,
  failed: 0,
  pending: 0,
  notRead: 0,
  sample: null,
  control: vi.fn().mockResolvedValue({}),
  refetch: vi.fn(),
  ...extra,
});

function Where() {
  const location = useLocation();
  return (
    <output data-testid="where">{location.pathname + location.search}</output>
  );
}

function renderRun(run, props = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter>
          <AskRunView
            projectId="project-1"
            run={run}
            unit={run.unit}
            {...props}
          />
          <Where />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

beforeEach(() => {
  http.get.mockReset();
  globalThis.sessionStorage?.clear?.();
});

describe("the live rows table with real paging and handoff", () => {
  it("loads the next page, filters from the chart, and hands over all selected HTTP rows", async () => {
    http.get.mockImplementation((url, { params }) => {
      expect(url).toContain("instant-eval/rows");
      expect(params).toMatchObject({
        project_id: "project-1",
        eval_task_id: "task-1",
        tz: "America/New_York",
      });
      if (params.answer === "No") {
        expect(params.config_id).toBe(CONFIG);
        if (params.compact) {
          return Promise.resolve(
            params.cursor === "handoff-next"
              ? response([row(52, "No")], 3)
              : response([row(50, "No"), row(51, "No")], 3, "handoff-next"),
          );
        }
        return Promise.resolve(
          response([row(50, "No"), row(51, "No"), row(52, "No")], 3),
        );
      }
      expect(params.page_size).toBe(ROWS_PAGE);
      return Promise.resolve(
        params.cursor === "table-next"
          ? response([row(50, "No"), row(51, "No"), row(52, "No")], 53)
          : response(
              Array.from({ length: ROWS_PAGE }, (_, i) => row(i)),
              53,
              "table-next",
            ),
      );
    });
    renderRun(runOf());
    const table = screen.getByRole("region", { name: "Sessions asked about" });
    await waitFor(() =>
      expect(within(table).getAllByRole("row")).toHaveLength(ROWS_PAGE + 1),
    );
    fireEvent.click(
      within(table).getByRole("button", {
        name: "Show 3 more · 50 of 53 shown",
      }),
    );
    await waitFor(() =>
      expect(within(table).getAllByRole("row")).toHaveLength(54),
    );
    expect(within(table).getByText("Customer question 52")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^No 6% · 3$/ }));
    await waitFor(() =>
      expect(
        http.get.mock.calls.some(([, { params }]) => params.answer === "No"),
      ).toBe(true),
    );
    await waitFor(
      () => expect(within(table).getAllByRole("row")).toHaveLength(4),
      { timeout: 10000 },
    );
    expect(within(table).queryByText("Customer question 0")).toBeNull();
    const selection = screen.getByRole("region", { name: "Selected rows" });
    expect(within(selection).getByText("3 sessions")).toBeInTheDocument();
    fireEvent.click(
      within(selection).getByRole("button", { name: "Find patterns" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("where")).toHaveTextContent(
        "/dashboard/observe/project-1/insights",
      ),
    );
    const url = new URL(
      screen.getByTestId("where").textContent,
      "http://app.test",
    );
    const handoff = readHandoff(url.searchParams.get("seed"));
    expect(handoff.population.map((item) => item.id)).toEqual([
      "s50",
      "s51",
      "s52",
    ]);
    expect(
      http.get.mock.calls.filter(([, { params }]) => params.compact),
    ).toHaveLength(2);
  });

  it("shows a refused HTTP read and retries it without substituting empty rows", async () => {
    http.get
      .mockRejectedValueOnce({ statusCode: 400, result: "Bad group" })
      .mockResolvedValue(response([row(0)], 1));
    renderRun(runOf());
    const table = screen.getByRole("region", { name: "Sessions asked about" });
    const retry = await within(table).findByRole("button", { name: "Retry" });
    expect(within(table).getByText(/couldn't be loaded/)).toBeInTheDocument();
    await act(async () => fireEvent.click(retry));
    expect(
      await within(table).findByText("Customer question 0"),
    ).toBeInTheDocument();
    expect(http.get).toHaveBeenCalledTimes(2);
  });
});

describe("cancelling a live run", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("counts the answers it keeps again while it asks", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    http.get.mockResolvedValue(response([], 0));
    const readNow = vi.fn();
    const live = (answers) => {
      const run = runOf({
        status: "running",
        total: 115,
        answered: answers,
        pending: 115 - answers,
        readNow,
      });
      run.summary = {
        ...run.summary,
        total: 115,
        counts: { [CONFIG]: answers ? { No: answers } : {} },
      };
      return run;
    };
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrap = (run) => (
      <QueryClientProvider client={client}>
        <ThemeProvider theme={theme}>
          <MemoryRouter>
            <AskRunView projectId="project-1" run={run} unit={run.unit} />
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    );
    const view = render(wrap(live(0)));
    // Polled at its usual pace, nothing extra is read before Cancel asks.
    expect(readNow).not.toHaveBeenCalled();
    // Red is kept for the confirmation, not the button that asks for it.
    expect(screen.getByRole("button", { name: "Stop" })).not.toHaveClass(
      "MuiButton-outlinedError",
    );
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(
      screen.getByText(
        "Stop this run? Nothing has been answered yet, and it can't be resumed.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop run" })).toHaveClass(
      "MuiButton-containedError",
    );
    // Read at once, then every 2 s while it asks.
    expect(readNow).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(readNow).toHaveBeenCalledTimes(3);
    // Sixteen answers landed meanwhile: the count it keeps says so.
    view.rerender(wrap(live(16)));
    expect(
      screen.getByText(
        "Stop this run? The 16 answers so far are kept; it can't be resumed.",
      ),
    ).toBeInTheDocument();
    // Back keeps it running and stops the extra reads.
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    act(() => {
      vi.advanceTimersByTime(10000);
    });
    expect(readNow).toHaveBeenCalledTimes(3);
    await waitFor(() => expect(http.get).toHaveBeenCalled());
  });
});

describe("the run's recovery and confidence headline", () => {
  it("puts low confidence in the user answer headline and explains mixed-session voting", async () => {
    http.get.mockResolvedValue(response([], 0));
    const run = runOf({ unit: "users", status: "done", total: 3, answered: 3 });
    run.summary = {
      ...run.summary,
      counts: { [CONFIG]: { Yes: 3 } },
      unsure: { [CONFIG]: { Yes: 2 } },
    };
    renderRun(run);
    expect(screen.getByLabelText("Answer summary")).toHaveTextContent(
      "3 Yes · 2 low-confidence",
    );
    expect(
      screen.getByText(
        /Low-confidence answers are included in these counts, not extra answers/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Each user counts once, with the most common answer across their answered sessions/,
      ),
    ).toHaveTextContent(/Ties use the latest session/);
    await waitFor(() => expect(http.get).toHaveBeenCalled());
  });

  it("shows failed before answering even when every attempted row failed, with fix and retry", async () => {
    http.get.mockResolvedValue(response([], 0));
    const retry = vi.fn();
    const run = runOf({ status: "failed", answered: 0, failed: 53 });
    run.summary.counts = { [CONFIG]: { __error__: 53 } };
    renderRun(run, { onRunAgain: retry });
    expect(screen.getByText("Failed before answering")).toBeInTheDocument();
    expect(
      screen.getByText(
        "No answers were saved. Check the evaluator or retry the run.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Fix evaluator" })).toHaveAttribute(
      "href",
      "/dashboard/evaluations/template-1",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(http.get).toHaveBeenCalled());
  });

  it("keeps answers saved to one question when another question failed", async () => {
    http.get.mockResolvedValue(response([], 0));
    const run = runOf({
      status: "failed",
      answered: 0,
      failed: 3,
      total: 3,
      questions: [
        QUESTION,
        { ...QUESTION, configId: "q2", question: "Satisfied?" },
      ],
    });
    run.summary.counts = { [CONFIG]: { Yes: 3 }, q2: { __error__: 3 } };
    renderRun(run, { onRunAgain: vi.fn() });
    expect(screen.getByLabelText("Answer summary")).toHaveTextContent("3 Yes");
    expect(screen.queryByText("Failed before answering")).toBeNull();
    expect(screen.queryByText(/No answers were saved/)).toBeNull();
    expect(
      screen.getByText("This run failed: 3 answers saved. Run it again."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "2 · Satisfied?" }));
    expect(screen.queryByText(/No answers were saved/)).toBeNull();
    await waitFor(() => expect(http.get).toHaveBeenCalled());
  });

  it("clears the answer filter when the question already shown is chosen again", async () => {
    http.get.mockResolvedValue(response([], 0));
    const run = runOf({
      status: "done",
      questions: [
        QUESTION,
        { ...QUESTION, configId: "q2", question: "Satisfied?" },
      ],
    });
    run.summary.counts = { [CONFIG]: { Yes: 50, No: 3 }, q2: { Yes: 53 } };
    renderRun(run);
    const table = screen.getByRole("region", { name: "Sessions asked about" });
    fireEvent.click(screen.getByRole("button", { name: /^No 6% · 3$/ }));
    expect(
      within(table).getByText("No · Was the issue resolved?"),
    ).toBeInTheDocument();
    const shown = screen.getByRole("tab", {
      name: "1 · Was the issue resolved?",
    });
    expect(shown).toHaveAttribute("aria-selected", "true");
    fireEvent.click(shown);
    expect(
      within(table).queryByText("No · Was the issue resolved?"),
    ).toBeNull();
    await waitFor(() => expect(http.get).toHaveBeenCalled());
  });

  it("reads more from the low-data heatmap using the run's control", async () => {
    http.get.mockResolvedValue(response([], 0));
    const run = runOf({
      status: "done",
      answered: 5,
      total: 5,
      sample: {
        is_sample: true,
        can_extend: true,
        population: 1000,
        requested: 5,
        read: 5,
        next_size: 5,
      },
    });
    run.summary = { ...run.summary, counts: { [CONFIG]: { Yes: 5 } } };
    renderRun(run);
    const heatmap = screen.getByRole("region", { name: "Heatmap of answers" });
    expect(
      within(heatmap).getByText("Not enough answers for time patterns"),
    ).toBeInTheDocument();
    expect(within(heatmap).queryByRole("combobox")).toBeNull();
    await act(async () =>
      fireEvent.click(
        within(heatmap).getByRole("button", { name: "Read more" }),
      ),
    );
    expect(run.control).toHaveBeenCalledWith("read_more", { more: 5 });
  });
});

describe("opening a user from a user question's table", () => {
  it("opens the user's sessions on the run's days, not on Today", async () => {
    http.get.mockResolvedValue(
      response(
        [
          {
            ...row(9),
            id: "u9",
            label: "cust_009",
            user_id: "cust_009",
            end_user_id: "cust_009",
            answers: {
              [CONFIG]: { answer: "No", counts: { No: 2 }, confidence: 0.9 },
            },
          },
        ],
        1,
      ),
    );
    const run = runOf({ unit: "users", status: "done", total: 1, answered: 1 });
    renderRun(run);
    const table = screen.getByRole("region", { name: "Users asked about" });
    await waitFor(() =>
      expect(within(table).getAllByRole("row")).toHaveLength(2),
    );
    fireEvent.click(within(table).getAllByRole("row")[1]);
    const url = new URL(
      screen.getByTestId("where").textContent,
      "http://app.test",
    );
    expect(url.pathname).toBe("/dashboard/users/cust_009");
    // The user page opens on Today, which misses older sessions.
    expect(url.searchParams.get("userTab")).toBe("sessions");
    const dates = JSON.parse(url.searchParams.get("sessionDateFilter"));
    expect(dates.dateOption).toBe("Custom");
    expect(dates.dateFilter.map((value) => new Date(value).getTime())).toEqual(
      run.window.map((value) => new Date(value).getTime()),
    );
  });
});

// One client across rerenders, so a run can move from state to state.
function runWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function wrap(run) {
    return (
      <QueryClientProvider client={client}>
        <ThemeProvider theme={theme}>
          <MemoryRouter>
            <AskRunView projectId="project-1" run={run} unit={run.unit} />
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    );
  };
}

describe("the run's rows table before any row is read", () => {
  it("waits while Jev reads, asks for a resume when paused or stalled, and says none match once over", async () => {
    http.get.mockResolvedValue(response([], 0));
    const unread = (status) => {
      const run = runOf({ status, answered: 0, pending: 53 });
      run.summary = { ...run.summary, counts: { [CONFIG]: {} } };
      return run;
    };
    const wrap = runWrapper();
    const view = render(wrap(unread("running")));
    const table = () =>
      screen.getByRole("region", { name: "Sessions asked about" });
    expect(
      await within(table()).findByText(
        "Answers will appear here as Jev reads each session.",
      ),
    ).toHaveAttribute("role", "status");
    expect(within(table()).queryByText("No sessions match.")).toBeNull();

    // Pausing still lands the answers in flight: keep waiting.
    view.rerender(wrap(unread("pausing")));
    expect(
      within(table()).getByText(
        "Answers will appear here as Jev reads each session.",
      ),
    ).toBeInTheDocument();

    // Paused, or stalled, nothing more is read until it is resumed.
    for (const status of ["paused", "stalled"]) {
      view.rerender(wrap(unread(status)));
      expect(within(table()).getByRole("status")).toHaveTextContent(
        "Resume the run to start reading sessions.",
      );
      expect(screen.getByRole("button", { name: "Resume" })).toBeEnabled();
    }

    // Over, with no rows: none match, and no chart filter to clear.
    view.rerender(wrap(unread("done")));
    expect(within(table()).getByText("No sessions match.")).toBeInTheDocument();
    expect(within(table()).queryByRole("status")).toBeNull();
    expect(
      within(table()).queryByRole("button", { name: "Clear filters" }),
    ).toBeNull();
  });

  it("offers Clear filters when a finished run's chart filter matches no row", async () => {
    http.get.mockImplementation((url, { params }) =>
      Promise.resolve(
        params.answer === "No" ? response([], 0) : response([row(0)], 53),
      ),
    );
    const wrap = runWrapper();
    render(wrap(runOf({ status: "done" })));
    const table = screen.getByRole("region", { name: "Sessions asked about" });
    expect(
      await within(table).findByText("Customer question 0"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^No 6% · 3$/ }));
    expect(
      await within(table).findByText("No sessions match.", undefined, {
        timeout: 10000,
      }),
    ).toBeInTheDocument();
    expect(within(table).queryByText(/Answers will appear here/)).toBeNull();
    fireEvent.click(
      within(table).getByRole("button", { name: "Clear filters" }),
    );
    expect(
      await within(table).findByText("Customer question 0"),
    ).toBeInTheDocument();
    expect(
      within(table).queryByText("No · Was the issue resolved?"),
    ).toBeNull();
  });
});

describe("a finished run with rows Jev couldn't answer", () => {
  const mixed = (extra = {}) => {
    const run = runOf({
      status: "done",
      answered: 50,
      failed: 3,
      ...extra,
    });
    run.summary = {
      ...run.summary,
      counts: { [CONFIG]: { Yes: 47, No: 3, __error__: 3 } },
    };
    return run;
  };

  it("reads as Completed, neutral, not as a success", async () => {
    http.get.mockResolvedValue(response([], 0));
    renderRun(mixed());
    const badge = screen.getByTitle(/3 rows couldn't be fully answered/);
    const dot = badge.firstElementChild;
    expect(dot).toHaveStyle({
      backgroundColor: statusColors(theme, "neutral").solid,
    });
    expect(dot).not.toHaveStyle({
      backgroundColor: statusColors(theme, "success").solid,
    });
    await waitFor(() => expect(http.get).toHaveBeenCalled());
  });

  it("still reads as failed when it answered nothing", async () => {
    http.get.mockResolvedValue(response([], 0));
    const run = runOf({ status: "done", answered: 0, failed: 53 });
    run.summary = { ...run.summary, counts: { [CONFIG]: { __error__: 53 } } };
    renderRun(run);
    const dot = screen.getByTitle(
      /Couldn't answer any of the 53 sessions/,
    ).firstElementChild;
    expect(dot).toHaveStyle({
      backgroundColor: statusColors(theme, "danger").solid,
    });
    await waitFor(() => expect(http.get).toHaveBeenCalled());
  });

  it("offers Retry rows as a neutral button, not a danger one", async () => {
    http.get.mockResolvedValue(response([], 0));
    const run = mixed();
    renderRun(run);
    const retry = screen.getByRole("button", { name: "Retry 3 rows" });
    expect(retry).toHaveClass("MuiButton-outlined");
    expect(retry).not.toHaveClass("MuiButton-outlinedError");
    fireEvent.click(retry);
    expect(run.control).toHaveBeenCalledWith("retry_failed", {});
    await waitFor(() => expect(http.get).toHaveBeenCalled());
  });

  it("offers the sample banner's Retry rows as a neutral button too", async () => {
    http.get.mockResolvedValue(response([], 0));
    const run = mixed({
      sample: {
        is_sample: true,
        can_extend: false,
        population: 1000,
        requested: 53,
        read: 53,
        next_size: 0,
      },
    });
    renderRun(run);
    // The estimate could move by more than the threshold, so the banner
    // carries the retry and the run controls don't repeat it.
    const retries = screen.getAllByRole("button", { name: "Retry 3 rows" });
    expect(retries).toHaveLength(1);
    expect(retries[0]).toHaveClass("MuiButton-outlined");
    expect(retries[0]).not.toHaveClass("MuiButton-outlinedError");
    expect(
      screen.getByText(/3 of 53 couldn't be answered/),
    ).toBeInTheDocument();
    await waitFor(() => expect(http.get).toHaveBeenCalled());
  });
});

describe("many questions at phone width", () => {
  // jsdom has no layout: MUI Tabs decides its arrows from an
  // IntersectionObserver on the first and last tab inside the scroller, so
  // lay the tabs out side by side in a 390px scroller and report which fit.
  const PHONE = 390;
  const TAB = 220;
  let restore = [];

  beforeEach(() => {
    vi.stubGlobal("innerWidth", PHONE);
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query) => {
        const max = /max-width:\s*([\d.]+)px/.exec(query);
        const min = /min-width:\s*([\d.]+)px/.exec(query);
        return {
          matches:
            (!max || PHONE <= Number(max[1])) &&
            (!min || PHONE >= Number(min[1])),
          media: query,
          onchange: null,
          addListener: vi.fn(),
          removeListener: vi.fn(),
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
          dispatchEvent: vi.fn(),
        };
      }),
    );
    const rect = (left, width) => ({
      left,
      right: left + width,
      width,
      top: 0,
      bottom: 32,
      height: 32,
      x: left,
      y: 0,
    });
    const original = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function getBoundingClientRect() {
      if (this.classList?.contains("MuiTabs-scroller")) return rect(0, PHONE);
      if (this.getAttribute?.("role") === "tab") {
        const index = Array.from(this.parentElement.children).indexOf(this);
        return rect(index * TAB, TAB);
      }
      return original.call(this);
    };
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        constructor(callback, { root }) {
          this.callback = callback;
          this.root = root;
        }

        observe(target) {
          const box = this.root.getBoundingClientRect();
          const tab = target.getBoundingClientRect();
          this.callback([
            {
              target,
              isIntersecting: tab.left >= box.left && tab.right <= box.right,
            },
          ]);
        }

        disconnect() {}

        unobserve() {}
      },
    );
    restore = [() => (Element.prototype.getBoundingClientRect = original)];
  });

  afterEach(() => {
    restore.forEach((undo) => undo());
    vi.unstubAllGlobals();
  });

  it("shows the question tabs' scroll arrows, not hidden on a phone", async () => {
    http.get.mockResolvedValue(response([], 0));
    const questions = Array.from({ length: 5 }, (_, i) => ({
      ...QUESTION,
      configId: `q${i}`,
      question: `Did the agent handle part ${i + 1} of the request?`,
    }));
    const run = runOf({ status: "done", questions });
    run.summary.counts = Object.fromEntries(
      questions.map((item) => [item.configId, { Yes: 50, No: 3 }]),
    );
    renderRun(run);
    const tablist = screen.getByRole("tablist", { name: "Questions" });
    expect(within(tablist).getAllByRole("tab")).toHaveLength(5);
    const tabs = tablist.closest(".MuiTabs-root");
    const arrows = tabs.querySelectorAll(".MuiTabs-scrollButtons");
    expect(arrows).toHaveLength(2);
    // MUI hides the arrows below `sm` unless the tabs allow them on mobile.
    arrows.forEach((arrow) =>
      expect(arrow).not.toHaveClass("MuiTabs-scrollButtonsHideMobile"),
    );
    // At the start: nothing to the left, more to the right.
    expect(arrows[0]).toHaveClass("Mui-disabled");
    expect(arrows[1]).not.toHaveClass("Mui-disabled");
    await waitFor(() => expect(http.get).toHaveBeenCalled());
  });
});

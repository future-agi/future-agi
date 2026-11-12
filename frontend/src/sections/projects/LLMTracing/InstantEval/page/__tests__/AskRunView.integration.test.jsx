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

describe("the rows table before the run's rows are read", () => {
  // What useAskRun gives before the run's summary arrives: a run still being
  // created (no task id yet), or a run opened by its link.
  const loadingRun = (extra = {}) => ({
    ...runOf(),
    status: "loading",
    summary: null,
    records: null,
    window: null,
    total: 0,
    answered: 0,
    failed: 0,
    pending: 0,
    notRead: 0,
    reads: null,
    ...extra,
  });

  const expectLoadingTable = () => {
    const table = screen.getByRole("region", { name: "Sessions asked about" });
    // The header and three placeholder rows, not an empty result.
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).queryByText(/match/)).toBeNull();
    expect(within(table).queryByText("0 sessions")).toBeNull();
  };

  it("shows the rows loading while the question is saved and the run starts", () => {
    http.get.mockResolvedValue(response([], 0));
    renderRun(
      loadingRun({
        taskId: null,
        questions: [{ ...QUESTION, configId: "" }],
      }),
      { creating: true },
    );
    expect(
      screen.getByText("Saving the question and starting Jev…"),
    ).toBeInTheDocument();
    expectLoadingTable();
  });

  it("shows the rows loading while a run opened by its link loads", () => {
    http.get.mockResolvedValue(response([], 0));
    renderRun(loadingRun({ questions: [] }));
    expect(screen.getByText("Loading the run…")).toBeInTheDocument();
    expectLoadingTable();
  });

  it("says when an answer matches no rows, and clears the selection from there", async () => {
    http.get.mockImplementation((url, { params }) =>
      Promise.resolve(
        params.answer === "No"
          ? response([], 0)
          : response([row(0), row(1)], 2),
      ),
    );
    renderRun(runOf());
    const table = screen.getByRole("region", { name: "Sessions asked about" });
    expect(
      await within(table).findByText("Customer question 0"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^No 6% · 3$/ }));
    expect(
      await within(table).findByText(
        "No sessions match this selection.",
        {},
        { timeout: 10000 },
      ),
    ).toBeInTheDocument();
    fireEvent.click(within(table).getByRole("button", { name: "Clear" }));
    expect(
      await within(table).findByText(
        "Customer question 0",
        {},
        { timeout: 10000 },
      ),
    ).toBeInTheDocument();
    expect(within(table).queryByText(/match/)).toBeNull();
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
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(
      screen.getByText(
        "Cancel this run? Nothing has been answered yet, and it can't be resumed.",
      ),
    ).toBeInTheDocument();
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
        "Cancel this run? The 16 answers so far are kept; it can't be resumed.",
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
    // Rows stays, so the run's answers can still be split by group.
    expect(
      within(heatmap).getByRole("combobox", { name: "Rows" }),
    ).toBeInTheDocument();
    expect(
      within(heatmap).queryByRole("combobox", { name: "Colour" }),
    ).toBeNull();
    await act(async () =>
      fireEvent.click(
        within(heatmap).getByRole("button", { name: "Read more" }),
      ),
    );
    expect(run.control).toHaveBeenCalledWith("read_more", { more: 5 });
  });
});

describe("sample read limits (W10-INSTANT-EVAL-L05)", () => {
  const sampledRun = (sample) => {
    const run = runOf({
      status: "done",
      unit: "traces",
      answered: 1000,
      total: 1000,
      sample: {
        is_sample: true,
        can_extend: true,
        can_read_all: false,
        population: 250000,
        requested: 1000,
        read: 1000,
        next_size: 1000,
        ...sample,
      },
    });
    run.summary = { ...run.summary, counts: { [CONFIG]: { Yes: 1000 } } };
    return run;
  };

  it("disables Read all above the run cap but keeps the bounded increment usable", async () => {
    http.get.mockResolvedValue(response([], 0));
    const run = sampledRun();
    renderRun(run);
    const readAll = screen.getByRole("button", { name: "Read all 250,000" });
    expect(readAll).toBeDisabled();
    expect(readAll).toHaveAttribute(
      "title",
      "This view exceeds the per-run row limit. Read a smaller increment or narrow the view.",
    );
    fireEvent.click(readAll);
    expect(run.control).not.toHaveBeenCalled();

    const readMore = screen.getByRole("button", { name: "Read 1,000 more" });
    expect(readMore).toBeEnabled();
    await act(async () => fireEvent.click(readMore));
    expect(run.control).toHaveBeenCalledExactlyOnceWith("read_more", {
      more: 1000,
    });
  });

  it.each([
    { population: 100000, next_size: 1000 },
    { population: 1500, next_size: 500 },
  ])(
    "keeps Read all usable when $population fits the run cap",
    async (sample) => {
      http.get.mockResolvedValue(response([], 0));
      const run = sampledRun({ ...sample, can_read_all: true });
      renderRun(run);
      const readAll = screen.getByRole("button", {
        name: `Read all ${sample.population.toLocaleString()}`,
      });
      expect(readAll).toBeEnabled();
      await act(async () => fireEvent.click(readAll));
      expect(run.control).toHaveBeenCalledExactlyOnceWith("read_more", {});
    },
  );

  it("does not offer another increment once the run cap is reached", async () => {
    http.get.mockResolvedValue(response([], 0));
    const run = sampledRun({
      requested: 100000,
      read: 100000,
      next_size: 0,
      can_extend: false,
    });
    renderRun(run);
    expect(
      screen.getByRole("button", { name: "Read all 250,000" }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: /^Read [\d,]+ more$/ }),
    ).toBeNull();
    await waitFor(() => expect(http.get).toHaveBeenCalled());
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

describe("exporting a run's rows as CSV", () => {
  it("shows the export under way, takes no second click, and says when it is done", async () => {
    let finish;
    const download = new Promise((resolve) => {
      finish = resolve;
    });
    http.get.mockImplementation((url) =>
      url.includes("instant-eval/export")
        ? download
        : Promise.resolve(response([row(0), row(1)], 2)),
    );
    const exports = () =>
      http.get.mock.calls.filter(([url]) =>
        url.includes("instant-eval/export"),
      );
    const create = vi.fn(() => "blob:csv");
    const revoke = vi.fn();
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;
    URL.createObjectURL = create;
    URL.revokeObjectURL = revoke;
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    try {
      renderRun(runOf({ status: "done" }));
      const table = screen.getByRole("region", {
        name: "Sessions asked about",
      });
      expect(
        await within(table).findByText("Customer question 1"),
      ).toBeInTheDocument();

      fireEvent.click(
        within(table).getByRole("button", { name: "Export CSV" }),
      );
      // A large file takes a while to arrive: its button says it is on its
      // way, and a second click doesn't start a second download.
      const busy = within(table).getByRole("button", {
        name: "Exporting CSV…",
      });
      expect(busy).toBeDisabled();
      expect(exports()).toHaveLength(1);
      fireEvent.click(busy);
      expect(exports()).toHaveLength(1);

      await act(async () => {
        finish({
          data: new Blob(["id,answer\ns0,Yes"], { type: "text/csv" }),
        });
      });
      expect(await screen.findByText("CSV exported.")).toBeInTheDocument();
      expect(
        within(table).getByRole("button", { name: "Export CSV" }),
      ).toBeEnabled();
      expect(click).toHaveBeenCalledTimes(1);
      await waitFor(() => expect(revoke).toHaveBeenCalledWith("blob:csv"), {
        timeout: 3000,
      });
    } finally {
      click.mockRestore();
      URL.createObjectURL = originalCreate;
      URL.revokeObjectURL = originalRevoke;
    }
  });
});

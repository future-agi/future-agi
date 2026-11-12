import React from "react";
import PropTypes from "prop-types";
import { act, renderHook } from "@testing-library/react";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), read: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: http.get, post: http.post },
  readQuery: http.read,
  endpoints: {
    project: {
      getEvalTaskConfig: () => "/configs",
      createEvalTaskConfig: () => "/config",
      createEvalTask: () => "/task",
      getEvalTaskList: () => "/tasks",
      instantEvalRuns: () => "/runs",
      instantEvalStart: () => "/start",
    },
    develop: { eval: { createEvalTemplateV2: "/template" } },
  },
}));

import {
  AskKeyMissing,
  LiveTaskUnchecked,
  askError,
  buildAnswerFilter,
  createContinuousTask,
  findContinuousTask,
  historyRefetchMs,
  historyRun,
  itemFromConfig,
  matchesSavedQuestion,
  startAskRun,
  useInstantEvalHistory,
} from "../useInstantEval";

const ok = (result) => ({ data: { status: true, result } });
const RANGE = ["2026-10-08T00:00:00.000Z", "2026-10-15T23:00:00.000Z"];
const DATE_ITEM = {
  column_id: "created_at",
  filter_config: {
    filter_type: "datetime",
    filter_op: "between",
    filter_value: RANGE,
  },
};
const STATUS_ITEM = {
  column_id: "status",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: "OK",
  },
};

const MODEL_ITEM = {
  column_id: "model",
  filter_config: {
    filter_type: "text",
    filter_op: "in",
    filter_value: ["gpt-4o-mini"],
  },
};
const QUESTION = {
  question: "Did the customer get what they came for?",
  answerType: "yes_no",
  categories: [],
};
// The server's refusal of a question on Jev without the organization's
// TypeSafe key (tracer/views/instant_eval_scale.py _jev_key_missing).
const KEY_MISSING = {
  message:
    "API key not configured for typesafe. Please add your API key in settings.",
  error_code: "JEV_KEY_MISSING",
  settings_path: "/dashboard/settings/ai-providers",
};

const getCalls = (url) =>
  http.get.mock.calls.filter(([called]) => called === url);
const postBody = (url) =>
  http.post.mock.calls.find(([called]) => called === url)?.[1];

const wrapperFor = (client) => {
  const Wrapper = ({ children }) =>
    React.createElement(QueryClientProvider, { client }, children);
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("startAskRun", () => {
  // The /start answer for a sample of 1,000 of a view of 24,473.
  const STARTED = {
    task_id: "task-1",
    rows_requested: 1000,
    population: 24473,
    is_sample: true,
  };
  // A backend that saves one evaluator ("c1") and starts task "task-1".
  const serve = ({ configs = [], start } = {}) => {
    http.get.mockImplementation((url) => {
      if (url === "/configs") return Promise.resolve(ok(configs));
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });
    http.post.mockImplementation((url) => {
      if (url === "/template") return Promise.resolve(ok({ id: "t1" }));
      if (url === "/config") return Promise.resolve(ok({ id: "c1" }));
      if (url === "/start")
        return start ? start() : Promise.resolve(ok(STARTED));
      return Promise.reject(new Error(`unexpected POST ${url}`));
    });
  };
  const ask = (args) =>
    startAskRun({ projectId: "p1", questions: [QUESTION], ...args });

  it("saves a new question as a Jev evaluator and starts a sample of the view", async () => {
    serve();
    const started = await ask({
      unit: "sessions",
      // Not a session read: a session is read whole.
      reads: "input_output",
      filters: [STATUS_ITEM, DATE_ITEM],
      sampleSize: 1000,
      datePreset: "7D",
      label: "Past 7D",
    });
    expect(postBody("/template")).toMatchObject({
      eval_type: "llm",
      model: "jev-latest",
      output_type: "deterministic",
      choice_scores: { Yes: 1, No: 0 },
      instructions:
        "Did the customer get what they came for?\n\nConversation:\n{{conversation}}",
    });
    const mapping = { conversation: "transcript_full" };
    expect(postBody("/config")).toEqual({
      project: "p1",
      eval_template: "t1",
      name: QUESTION.question,
      model: "jev-latest",
      mapping,
      error_localizer: false,
      config: {
        mapping,
        run_config: { model: "jev-latest" },
        instant_eval: {
          question: QUESTION.question,
          answer_type: "yes_no",
          labels: ["Yes", "No"],
          reads: "transcript_full",
          unit: "sessions",
        },
      },
    });
    // The view's filters as the grid sent them, window included.
    expect(postBody("/start")).toEqual({
      project_id: "p1",
      unit: "sessions",
      filters: [STATUS_ITEM, DATE_ITEM],
      sample_size: 1000,
      config_ids: ["c1"],
      confirm: false,
      date_preset: "7D",
      label: "Past 7D",
    });
    expect(started).toEqual({
      taskId: "task-1",
      questions: [
        expect.objectContaining({
          configId: "c1",
          templateId: "t1",
          question: QUESTION.question,
          reads: "transcript_full",
          unit: "sessions",
        }),
      ],
      rows: 1000,
      population: 24473,
      isSample: true,
    });
  });

  it("sends the rows a confirm covered, so the server can ask again", async () => {
    serve();
    await ask({ unit: "users", confirm: true, confirmedRows: 6000 });
    expect(postBody("/start")).toMatchObject({
      confirm: true,
      confirmed_rows: 6000,
    });
    http.post.mockClear();
    // Nothing confirmed, nothing claimed.
    await ask({ unit: "users", confirm: false, confirmedRows: 6000 });
    expect(postBody("/start")).not.toHaveProperty("confirmed_rows");
  });

  it("reads whole sessions, steps included, when asked to", async () => {
    serve();
    await ask({ unit: "sessions", reads: "transcript_full" });
    expect(postBody("/template").instructions).toBe(
      "Did the customer get what they came for?\n\nConversation:\n{{conversation}}",
    );
    expect(postBody("/config").mapping).toEqual({
      conversation: "transcript_full",
    });
    expect(postBody("/config").config.instant_eval.reads).toBe(
      "transcript_full",
    );
  });

  it("reads whole traces, and a span's own output", async () => {
    serve();
    await ask({ unit: "traces", reads: "trace_tree" });
    expect(postBody("/template").instructions).toContain("{{whole_trace}}");
    expect(postBody("/config").mapping).toEqual({ whole_trace: "trace_tree" });
    expect(postBody("/config").config.instant_eval).toMatchObject({
      unit: "traces",
      reads: "trace_tree",
    });
    expect(postBody("/start").unit).toBe("traces");

    vi.clearAllMocks();
    serve();
    await ask({ unit: "spans", reads: "output" });
    expect(postBody("/template").instructions).toBe(
      "Did the customer get what they came for?\n\nSpan output:\n{{output}}",
    );
    expect(postBody("/config").mapping).toEqual({ output: "output" });
    expect(postBody("/config").config.instant_eval).toMatchObject({
      unit: "spans",
      reads: "output",
    });
  });

  it("reuses the project's evaluator for the same question, naming a new one apart", async () => {
    const saved = {
      id: "c7",
      name: QUESTION.question,
      eval_template: "t7",
      config: {
        instant_eval: {
          question: QUESTION.question,
          answer_type: "yes_no",
          labels: ["Yes", "No"],
          reads: "transcript",
          unit: "sessions",
        },
      },
    };
    serve({ configs: [saved] });
    await ask({ unit: "sessions", reads: "transcript", confirm: true });
    expect(postBody("/template")).toBeUndefined();
    expect(postBody("/config")).toBeUndefined();
    expect(postBody("/start")).toMatchObject({
      config_ids: ["c7"],
      confirm: true,
      sample_size: null,
    });

    // Asked of users it is another question; its name is taken.
    vi.clearAllMocks();
    serve({ configs: [saved] });
    await ask({ unit: "users", reads: "transcript" });
    expect(postBody("/config").name).toBe(`${QUESTION.question} (2)`);
    expect(postBody("/config").config.instant_eval.unit).toBe("users");
    expect(postBody("/start").config_ids).toEqual(["c1"]);
  });

  it("reuses the newest evaluator of a question asked twice", async () => {
    const saved = (id) => ({
      id,
      name: QUESTION.question,
      eval_template: `t-${id}`,
      config: {
        instant_eval: {
          question: QUESTION.question,
          answer_type: "yes_no",
          labels: ["Yes", "No"],
          reads: "transcript",
          unit: "sessions",
        },
      },
    });
    // The list comes back newest first.
    serve({ configs: [saved("c-new"), saved("c-old")] });
    await ask({ unit: "sessions", reads: "transcript", confirm: true });
    expect(postBody("/start").config_ids).toEqual(["c-new"]);
  });

  it("asks a saved question again without saving it twice", async () => {
    serve();
    await startAskRun({
      projectId: "p1",
      unit: "sessions",
      existingItems: [
        {
          configId: "c9",
          question: QUESTION.question,
          answerType: "yes_no",
          labels: ["Yes", "No"],
          reads: "transcript",
        },
      ],
      filters: [DATE_ITEM],
      confirm: true,
    });
    expect(getCalls("/configs")).toHaveLength(0);
    expect(postBody("/template")).toBeUndefined();
    expect(postBody("/config")).toBeUndefined();
    expect(postBody("/start")).toMatchObject({
      config_ids: ["c9"],
      filters: [DATE_ITEM],
      confirm: true,
    });
  });

  it("names the rows handed over, reading users inside the view's window", async () => {
    serve();
    await ask({
      unit: "sessions",
      ids: ["s1", "s2"],
      filters: [STATUS_ITEM, DATE_ITEM],
      search: "refund",
      sampleSize: 1000,
    });
    expect(postBody("/start")).toMatchObject({
      unit: "sessions",
      ids: ["s1", "s2"],
      filters: [],
      sample_size: null,
    });
    expect(postBody("/start")).not.toHaveProperty("search");

    vi.clearAllMocks();
    serve();
    await ask({ unit: "users", ids: ["u1"], filters: [DATE_ITEM] });
    expect(postBody("/start")).toMatchObject({
      unit: "users",
      ids: ["u1"],
      filters: [DATE_ITEM],
      sample_size: null,
    });
  });

  it("starts nothing when the question couldn't be saved", async () => {
    serve();
    http.post.mockImplementation((url) =>
      url === "/start" ? Promise.resolve(ok(STARTED)) : Promise.resolve(ok({})),
    );
    await expect(ask({ unit: "traces" })).rejects.toThrow(
      "The question could not be saved as an evaluator.",
    );
    expect(postBody("/start")).toBeUndefined();
  });

  it("says a start on Jev needs the organization's TypeSafe key", async () => {
    serve({
      start: () =>
        Promise.reject({
          status: false,
          code: "JEV_KEY_MISSING",
          message: KEY_MISSING.message,
          statusCode: 400,
          result: KEY_MISSING,
        }),
    });
    const refused = await ask({ unit: "traces" }).catch((error) => error);
    expect(refused).toBeInstanceOf(AskKeyMissing);
    expect(refused.message).toBe(KEY_MISSING.message);
    expect(refused.settingsPath).toBe("/dashboard/settings/ai-providers");
    // The questions were saved, so asking again reuses them.
    expect(refused.questions).toEqual([
      expect.objectContaining({ configId: "c1" }),
    ]);
  });
});

describe("Keep answering", () => {
  // The project's evaluators, as list_custom_eval_configs returns them.
  const CONFIGS = [
    {
      id: "c1",
      name: "Did the user get help?",
      eval_template: "t1",
      config: {
        instant_eval: {
          question: "Did the user get help?",
          answer_type: "yes_no",
          labels: ["Yes", "No"],
          reads: "input_output",
          unit: "traces",
        },
      },
    },
    {
      id: "c2",
      name: "Why did they call?",
      eval_template: "t2",
      config: {
        instant_eval: {
          question: "Why did they call?",
          answer_type: "category",
          labels: ["Refund", "Shipping"],
          reads: "input_output",
          unit: "traces",
        },
      },
    },
  ];
  // The questions as the run page has them: from the run's summary, which
  // carries config ids but no evaluator names.
  const QUESTIONS = [
    {
      configId: "c1",
      question: "Did the user get help?",
      fullQuestion: "Did the user get help?",
      answerType: "yes_no",
      labels: ["Yes", "No"],
      definitions: {},
    },
  ];
  // One row of list_eval_tasks, as _serialize_eval_task_list_page writes it.
  const taskRow = (id, extra = {}) => ({
    id,
    name: "Instant eval (live) · Did the user get help?",
    status: "running",
    run_type: "continuous",
    row_type: "traces",
    filters_applied: { project_id: "p1", filters: [STATUS_ITEM] },
    created_at: "2026-10-15T10:00:00Z",
    evals_applied: ["Did the user get help?"],
    sampling_rate: 100,
    last_run: "2026-10-15T10:05:00Z",
    ...extra,
  });
  // The run's view as the grid sends it: its filters and its window.
  const VIEW = [STATUS_ITEM, DATE_ITEM];
  const find = (extra = {}) =>
    findContinuousTask({
      projectId: "p1",
      items: QUESTIONS,
      scopeFilters: VIEW,
      userId: "u1",
      ...extra,
    });
  const serveTasks = (pages, configs = CONFIGS) => {
    http.get.mockImplementation((url, { params } = {}) => {
      if (url === "/configs") return Promise.resolve(ok(configs));
      if (url === "/tasks")
        return Promise.resolve(
          ok({
            metadata: { total_rows: pages.flat().length },
            table: pages[params.page_number] || [],
            config: [],
          }),
        );
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });
  };

  it("finds the live task already answering the same questions", async () => {
    const live = taskRow("live-1");
    serveTasks([
      [
        // Same name, but a past run, a dead task, or other questions.
        taskRow("old", { run_type: "historical", status: "completed" }),
        taskRow("dead", { status: "failed" }),
        taskRow("more", {
          evals_applied: ["Did the user get help?", "Why did they call?"],
        }),
        live,
      ],
    ]);
    expect(await find()).toEqual(live);
    expect(http.get).toHaveBeenCalledWith("/configs", {
      params: { project_id: "p1" },
    });
    expect(getCalls("/tasks")[0][1].params).toMatchObject({
      project_id: "p1",
      name: "Instant eval (live)",
      page_number: 0,
    });
  });

  it("reads past the first page, and finds none when the list ends", async () => {
    const other = (n) =>
      taskRow(`other-${n}`, { evals_applied: ["Why did they call?"] });
    const full = Array.from({ length: 50 }, (_, n) => other(n));
    serveTasks([full, [other(50), taskRow("live-2")]]);
    expect((await find()).id).toBe("live-2");

    vi.clearAllMocks();
    serveTasks([full, [other(50)]]);
    expect(await find()).toBeUndefined();
    expect(getCalls("/tasks")).toHaveLength(2);
  });

  it("refuses rather than guess when the questions can't be matched", async () => {
    serveTasks([[taskRow("live-1")]]);
    // A question with no saved evaluator, or one deleted since.
    await expect(
      find({ items: [{ ...QUESTIONS[0], configId: "" }] }),
    ).rejects.toBeInstanceOf(LiveTaskUnchecked);
    await expect(
      find({ items: [{ ...QUESTIONS[0], configId: "gone" }] }),
    ).rejects.toThrow(/evaluators is gone/);
    // A list longer than it reads: it never says "none".
    const full = Array.from({ length: 50 }, (_, n) =>
      taskRow(`other-${n}`, { evals_applied: ["Why did they call?"] }),
    );
    serveTasks(Array.from({ length: 25 }, () => full));
    await expect(find()).rejects.toBeInstanceOf(LiveTaskUnchecked);
    expect(getCalls("/tasks")).toHaveLength(20);
  });

  it("treats the same questions over another view as another task", async () => {
    // A live task over Status = OK, and one over the whole project.
    serveTasks([
      [
        taskRow("over-status"),
        taskRow("over-all", { filters_applied: { project_id: "p1" } }),
      ],
    ]);
    // Another filter, or none: neither answers this view's new traces.
    expect(
      (await find({ scopeFilters: [MODEL_ITEM, DATE_ITEM] }))?.id,
    ).toBeUndefined();
    expect(
      (await find({ scopeFilters: [STATUS_ITEM, MODEL_ITEM] }))?.id,
    ).toBeUndefined();
    // No filters but the window: the whole project's task.
    expect((await find({ scopeFilters: [DATE_ITEM] })).id).toBe("over-all");
  });

  it("finds an exact duplicate whatever its filters' order, so starts none", async () => {
    const MINE = {
      column_id: "my_annotations",
      filter_config: { filter_type: "boolean", filter_op: "equals" },
    };
    serveTasks([
      [
        taskRow("live-1", {
          filters_applied: {
            project_id: "p1",
            // As the server saved them: "My annotations" bound to its user.
            filters: [
              MODEL_ITEM,
              {
                ...MINE,
                filter_config: { ...MINE.filter_config, user_id: "u1" },
              },
              STATUS_ITEM,
            ],
          },
        }),
      ],
    ]);
    const reordered = {
      filter_config: { ...STATUS_ITEM.filter_config },
      column_id: STATUS_ITEM.column_id,
    };
    expect(
      (await find({ scopeFilters: [DATE_ITEM, MINE, reordered, MODEL_ITEM] }))
        .id,
    ).toBe("live-1");
  });

  it("matches a task the server saved in its own form", async () => {
    const A = "0b6a5c1e-9a4e-4a3f-8f2a-1c2d3e4f5a6b";
    const B = "1c7b6d2f-0b5f-4b40-9a3b-2d3e4f5a6b7c";
    const PASTED = {
      column_id: "session_id",
      filter_config: {
        filter_type: "String",
        filter_op: "in",
        filter_value: [`${A}, ${B}`],
      },
    };
    // The server lowercases and de-aliases the type and splits pasted ids.
    serveTasks([
      [
        taskRow("live-1", {
          filters_applied: {
            project_id: "p1",
            filters: [
              {
                column_id: "session_id",
                filter_config: {
                  filter_type: "text",
                  filter_op: "in",
                  filter_value: [A, B],
                },
              },
            ],
          },
        }),
      ],
    ]);
    expect((await find({ scopeFilters: [PASTED, DATE_ITEM] })).id).toBe(
      "live-1",
    );
  });

  it("keeps a trace task and a span task apart", async () => {
    serveTasks([[taskRow("spans-1", { row_type: "spans" })]]);
    expect(await find()).toBeUndefined();
    expect((await find({ unit: "spans" })).id).toBe("spans-1");
  });

  it("matches My annotations only to the same user's task", async () => {
    const MINE = {
      column_id: "my_annotations",
      filter_config: { filter_type: "boolean", filter_op: "equals" },
    };
    serveTasks([
      [
        taskRow("theirs", {
          filters_applied: {
            project_id: "p1",
            filters: [
              {
                ...MINE,
                filter_config: { ...MINE.filter_config, user_id: "u2" },
              },
            ],
          },
        }),
      ],
    ]);
    // Another user's annotations are another view.
    expect(await find({ scopeFilters: [MINE, DATE_ITEM] })).toBeUndefined();
    expect(
      (await find({ scopeFilters: [MINE, DATE_ITEM], userId: "u2" })).id,
    ).toBe("theirs");
    // Without knowing who is asking, it can't tell.
    await expect(
      find({ scopeFilters: [MINE, DATE_ITEM], userId: undefined }),
    ).rejects.toBeInstanceOf(LiveTaskUnchecked);
  });

  it("starts a live task over the view's filters, without its window", async () => {
    http.post.mockResolvedValue(ok({ id: "live-3" }));
    expect(
      await createContinuousTask({
        projectId: "p1",
        items: QUESTIONS,
        scopeFilters: [STATUS_ITEM, DATE_ITEM],
        unit: "spans",
      }),
    ).toBe("live-3");
    expect(http.post).toHaveBeenCalledWith("/task", {
      name: "Instant eval (live) · Did the user get help?",
      project: "p1",
      evals: ["c1"],
      filters: { filters: [STATUS_ITEM] },
      run_type: "continuous",
      row_type: "spans",
      sampling_rate: 100,
    });
  });
});

describe("History", () => {
  const configsById = {
    c1: {
      configId: "c1",
      name: "Refund promised?",
      question: "Refund promised?",
      answerType: "yes_no",
      labels: ["Yes", "No"],
      unit: "sessions",
      reads: "transcript",
      templateId: "t1",
    },
  };
  const run = (taskId, extra = {}) => ({
    task_id: taskId,
    name: "Instant eval · Refund promised?",
    created_at: "2026-10-15T10:00:00Z",
    status: "completed",
    run_type: "historical",
    unit: "sessions",
    reads: "transcript",
    rows: 6,
    config_ids: ["c1"],
    questions: ["Refund promised?"],
    counts: { c1: { Yes: 4, No: 2 } },
    answered: 6,
    pending: 0,
    ...extra,
  });

  it("rebuilds a past run from the runs endpoint and its evaluators", () => {
    expect(
      historyRun(
        run("k1", { last_activity_at: "2026-10-15T10:01:00Z" }),
        configsById,
      ),
    ).toEqual({
      taskId: "k1",
      name: "Instant eval · Refund promised?",
      status: "completed",
      createdAt: "2026-10-15T10:00:00Z",
      unit: "sessions",
      reads: "transcript",
      rows: 6,
      // Sessions read: a user question's only.
      sessions: null,
      items: [configsById.c1],
      counts: { c1: { Yes: 4, No: 2 } },
      answered: 6,
      pending: 0,
      // When it last moved, for telling a live run that has stopped moving.
      lastActivityAt: "2026-10-15T10:01:00Z",
    });
    // An evaluator that is gone still reads from what the run counted.
    expect(
      historyRun(
        run("k2", {
          config_ids: ["gone"],
          questions: ["Why did they call?"],
          counts: { gone: { Refund: 3, Shipping: 1, __error__: 1 } },
        }),
        configsById,
      ).items,
    ).toEqual([
      {
        configId: "gone",
        question: "Why did they call?",
        answerType: "category",
        labels: ["Refund", "Shipping"],
        definitions: {},
      },
    ]);
  });

  it("is read again while a run is live, never for a live task", () => {
    const done = run("done");
    expect(historyRefetchMs([{ runs: [done] }])).toBe(false);
    expect(
      historyRefetchMs([
        { runs: [done] },
        { runs: [run("live", { status: "running", pending: 40 })] },
      ]),
    ).toBe(5000);
    // A "Keep answering" task stays running for as long as it lives.
    expect(
      historyRefetchMs([
        {
          runs: [
            done,
            run("keep", {
              run_type: "continuous",
              status: "running",
              pending: 3,
            }),
          ],
        },
      ]),
    ).toBe(false);
    expect(historyRefetchMs(undefined)).toBe(false);
  });

  it("reads this view's runs page by page, searching by question", async () => {
    http.get.mockResolvedValue(
      ok({
        runs: [
          run("new"),
          run("old", { created_at: "2026-10-14T10:00:00Z" }),
          run("live", { run_type: "continuous" }),
        ],
        has_more: true,
      }),
    );

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = wrapperFor(client);
    const { result, rerender } = renderHook(
      ({ search }) =>
        useInstantEvalHistory("p1", configsById, { unit: "sessions", search }),
      { wrapper, initialProps: { search: "" } },
    );
    await vi.waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.runs.map((runItem) => runItem.taskId)).toEqual([
      "new",
      "old",
    ]);
    expect(result.current.hasMore).toBe(true);
    expect(http.get).toHaveBeenCalledWith("/runs", {
      params: {
        project_id: "p1",
        unit: "sessions",
        page_number: 0,
        page_size: 20,
      },
      signal: expect.anything(),
    });
    // No eval-task list scan.
    expect(getCalls("/tasks")).toHaveLength(0);

    rerender({ search: "refund" });
    await vi.waitFor(() =>
      expect(http.get).toHaveBeenLastCalledWith("/runs", {
        params: {
          project_id: "p1",
          unit: "sessions",
          search: "refund",
          page_number: 0,
          page_size: 20,
        },
        signal: expect.anything(),
      }),
    );
    await vi.waitFor(() => expect(result.current.hasMore).toBe(true));
    await act(() => result.current.loadMore());
    expect(http.get.mock.calls.at(-1)[1].params.page_number).toBe(1);
  });

  it("leaves a failed read to the page, with no toast of its own", async () => {
    // A deleted project: the app's query cache toasts any read that doesn't
    // say it handles its own failure.
    const notFound = { statusCode: 404, result: "Project not found" };
    http.get.mockRejectedValue(notFound);
    const toasts = vi.fn();
    const client = new QueryClient({
      queryCache: new QueryCache({
        onError: (error, query) => {
          if (!query.options.meta?.errorHandled) toasts(error);
        },
      }),
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(
      () => useInstantEvalHistory("p1", configsById),
      { wrapper: wrapperFor(client) },
    );
    await vi.waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBe(notFound);
    expect(toasts).not.toHaveBeenCalled();
  });
});

describe("filters and saved questions", () => {
  it("builds the EVAL_METRIC filter the trace grid understands", () => {
    expect(
      buildAnswerFilter({ configId: "c1", question: "Refund promised?" }, [
        "Yes",
      ]),
    ).toEqual({
      column_id: "c1",
      property_id: "eval_config:c1",
      display_name: "Refund promised?",
      filter_config: {
        filter_type: "text",
        filter_op: "in",
        filter_value: ["Yes"],
        col_type: "EVAL_METRIC",
      },
    });
  });

  it("only treats configs created by Instant eval as saved questions", () => {
    expect(itemFromConfig({ id: "c1", config: {} })).toBeNull();
    expect(
      itemFromConfig({
        id: "c2",
        name: "Refund promised?",
        eval_template: "t2",
        config: {
          instant_eval: {
            question: "Refund promised?",
            answer_type: "yes_no",
            labels: ["Yes", "No"],
            reads: "input_output",
          },
        },
      }),
    ).toEqual({
      configId: "c2",
      name: "Refund promised?",
      templateId: "t2",
      question: "Refund promised?",
      answerType: "yes_no",
      labels: ["Yes", "No"],
      definitions: {},
      reads: "input_output",
      // Saved before sessions and users had Instant eval.
      unit: "traces",
    });
  });

  it("reuses a saved question only for the same unit and inputs", () => {
    const saved = {
      question: "Did they get what they came for?",
      answerType: "yes_no",
      labels: ["Yes", "No"],
      definitions: {},
      reads: "transcript",
      unit: "sessions",
    };
    const asked = {
      text: "did they get what they came for?",
      answerType: "yes_no",
      labels: ["Yes", "No"],
      definitions: {},
      reads: "transcript",
    };
    expect(matchesSavedQuestion(saved, { ...asked, unit: "sessions" })).toBe(
      true,
    );
    expect(matchesSavedQuestion(saved, { ...asked, unit: "users" })).toBe(
      false,
    );
    expect(
      matchesSavedQuestion(saved, {
        ...asked,
        unit: "sessions",
        definitions: { Yes: "Their issue was resolved" },
      }),
    ).toBe(false);
  });
});

describe("refusals the page explains", () => {
  // As the app's axios hands them over: the error envelope and its status.
  const envelope = (statusCode, code, result) => ({
    status: false,
    code,
    message: result.message,
    statusCode,
    result,
  });

  it("reads the app's error envelope as well as a raw Axios error", () => {
    const cancelled = askError(
      envelope(409, "instant_eval_cancelled", {
        message:
          "This run was cancelled. Use Run again to ask these questions again.",
        error_code: "INSTANT_EVAL_CANCELLED",
      }),
    );
    expect(cancelled.name).toBe("AskRefused");
    expect(cancelled.message).toMatch(/^This run was cancelled/);

    const tooLarge = askError(
      envelope(400, "instant_eval_sample_too_large", {
        message: "There are only 40 sessions in this view; choose up to 40.",
        error_code: "INSTANT_EVAL_SAMPLE_TOO_LARGE",
        sample_size: 40,
      }),
    );
    expect(tooLarge.name).toBe("AskSampleTooLarge");
    expect(tooLarge.sampleSize).toBe(40);

    const confirm = askError({
      response: {
        status: 409,
        data: {
          result: {
            error_code: "INSTANT_EVAL_CONFIRM",
            rows_to_read: 6000,
            credits_estimate: 360,
          },
        },
      },
    });
    expect(confirm.name).toBe("AskNeedsConfirm");
    expect(confirm.rows).toBe(6000);

    const counting = askError({
      status: false,
      code: "users_still_counting",
      message: "Still counting the users these filters match.",
      statusCode: 503,
    });
    expect(counting.name).toBe("AskRefused");
    expect(counting.message).toBe(
      "Still counting the users these filters match.",
    );

    const other = { statusCode: 500, message: "boom" };
    expect(askError(other)).toBe(other);
  });

  it("points a refusal for a missing TypeSafe key to AI Providers", () => {
    // A read more, resume or retry, as the app's axios hands it over.
    const control = askError(envelope(400, "JEV_KEY_MISSING", KEY_MISSING));
    expect(control).toBeInstanceOf(AskKeyMissing);
    expect(control.message).toBe(KEY_MISSING.message);
    expect(control.settingsPath).toBe("/dashboard/settings/ai-providers");
    // As a raw Axios error, and without words of its own.
    const raw = askError({
      response: {
        status: 400,
        data: {
          code: "JEV_KEY_MISSING",
          result: { error_code: "JEV_KEY_MISSING" },
        },
      },
    });
    expect(raw).toBeInstanceOf(AskKeyMissing);
    expect(raw.message).toMatch(/TypeSafe key/);
    // Another 400 is left as it is.
    const other = envelope(400, "bad_request", { message: "No rows failed." });
    expect(askError(other)).toBe(other);
  });
});

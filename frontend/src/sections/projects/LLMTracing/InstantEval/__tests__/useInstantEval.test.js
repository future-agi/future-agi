import React from "react";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), read: vi.fn() }));
const population = vi.hoisted(() => ({ collect: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: http.get, post: http.post },
  readQuery: http.read,
  endpoints: {
    project: {
      getEvalTaskConfig: () => "/configs",
      createEvalTaskConfig: () => "/config",
      createEvalTask: () => "/task",
      getEvalTaskLogs: () => "/logs",
      getEvalTaskList: () => "/tasks",
      instantEvalAnswers: () => "/answers",
      instantEvalRuns: () => "/runs",
      instantEvalUserSessions: () => "/user-sessions",
      getTracesForObserveProject: () => "/traces",
    },
    develop: { eval: { createEvalTemplateV2: "/template" } },
  },
}));
vi.mock("src/sections/projects/observePopulation", async (importOriginal) => ({
  ...(await importOriginal()),
  collectPopulation: population.collect,
}));

import {
  buildAnswerFilter,
  historyRun,
  itemFromConfig,
  matchesSavedQuestion,
  startAsk,
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
const U1 = "5fd6ed2b-9f0c-577b-99fd-63f543ca3f20";
const U2 = "d6126f89-131e-59fd-a9ac-9a1448c03c0c";
const QUESTION = {
  question: "Did the customer get what they came for?",
  answerType: "yes_no",
  categories: [],
};

// A backend that saves one evaluator ("c1") and starts task "task-1".
function serve({ configs = [], userSessions } = {}) {
  http.get.mockImplementation((url) => {
    if (url === "/configs") return Promise.resolve(ok(configs));
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  http.post.mockImplementation((url) => {
    if (url === "/template") return Promise.resolve(ok({ id: "t1" }));
    if (url === "/config") return Promise.resolve(ok({ id: "c1" }));
    if (url === "/task") return Promise.resolve(ok({ id: "task-1" }));
    if (url === "/user-sessions") return Promise.resolve(ok(userSessions));
    return Promise.reject(new Error(`unexpected POST ${url}`));
  });
}

const postBody = (url) =>
  http.post.mock.calls.find(([called]) => called === url)?.[1];
const getCalls = (url) =>
  http.get.mock.calls.filter(([called]) => called === url);

const ask = (args) =>
  startAsk({ projectId: "p1", questions: [QUESTION], ...args });

beforeEach(() => {
  vi.clearAllMocks();
});

describe("startAsk", () => {
  it("names the sessions handed over in one sessions task", async () => {
    serve();
    const items = [
      { id: "s1", time: RANGE[0], user_id: "cust_a" },
      { id: "s2", time: RANGE[1], user_id: "cust_b" },
    ];
    const started = await ask({
      unit: "sessions",
      reads: "input_output",
      items,
      filters: [STATUS_ITEM, DATE_ITEM],
    });
    expect(started).toMatchObject({ taskId: "task-1", items });
    expect(started.questions[0]).toMatchObject({ configId: "c1" });
    // Sessions read their conversation, whatever was asked for.
    expect(postBody("/template").instructions).toContain("{{transcript}}");
    expect(postBody("/config").mapping).toEqual({ transcript: "transcript" });
    expect(postBody("/config").config.instant_eval).toMatchObject({
      unit: "sessions",
      reads: "transcript",
    });
    expect(postBody("/task")).toMatchObject({
      evals: ["c1"],
      filters: { session_id: ["s1", "s2"] },
      row_type: "sessions",
      run_type: "historical",
      spans_limit: 2,
      sampling_rate: 100,
    });
    // The rows are never read again: the page already has them.
    expect(population.collect).not.toHaveBeenCalled();
  });

  it("reads whole sessions, steps included, when asked to", async () => {
    serve();
    await ask({
      unit: "sessions",
      reads: "transcript_full",
      items: [{ id: "s1" }],
    });
    expect(postBody("/template").instructions).toBe(
      "Did the customer get what they came for?\n\nConversation:\n{{conversation}}",
    );
    expect(postBody("/config").mapping).toEqual({
      conversation: "transcript_full",
    });
  });

  it("reads whole traces and each span on its own", async () => {
    serve();
    await ask({ unit: "traces", reads: "trace_tree", items: [{ id: "t1" }] });
    expect(postBody("/config").mapping).toEqual({ whole_trace: "trace_tree" });
    expect(postBody("/task")).toMatchObject({
      row_type: "traces",
      filters: { trace_id: ["t1"] },
    });
    vi.clearAllMocks();
    serve();
    await ask({ unit: "spans", reads: "output", items: [{ id: "sp1" }] });
    expect(postBody("/template").instructions).toContain("Span output:");
    expect(postBody("/task")).toMatchObject({
      row_type: "spans",
      filters: { span_id: ["sp1"] },
      spans_limit: 1,
    });
  });

  it("asks about each user's latest sessions, keeping users who had one", async () => {
    serve({
      userSessions: {
        sessions: [
          { session_id: "s1", end_user_id: U1 },
          { session_id: "s2", end_user_id: U1 },
        ],
        users: 1,
      },
    });
    const started = await ask({
      unit: "users",
      items: [
        { id: U1, user_id: "cust_kai" },
        { id: U2 },
        { id: "not-a-uuid" },
      ],
      filters: [DATE_ITEM],
    });
    expect(postBody("/user-sessions")).toEqual({
      project_id: "p1",
      end_user_ids: [U1, U2],
      per_user: 5,
      window: RANGE,
    });
    expect(postBody("/task")).toMatchObject({
      filters: { session_id: ["s1", "s2"] },
      row_type: "sessions",
    });
    expect(postBody("/config").config.instant_eval.unit).toBe("users");
    expect(started.items).toEqual([{ id: U1, user_id: "cust_kai" }]);
    expect(started.sessions).toBe(2);
  });

  it("starts nothing when there is nothing to ask about", async () => {
    serve({ userSessions: { sessions: [], users: 0 } });
    expect(await ask({ unit: "sessions", items: [] })).toEqual({
      empty: "There are no sessions in this view to ask about.",
    });
    expect(
      await ask({ unit: "users", items: [{ id: U1 }], filters: [DATE_ITEM] }),
    ).toEqual({ empty: "None of these users had a session in this window." });
    expect(postBody("/task")).toBeUndefined();
  });

  it("asks a saved question again without saving it twice", async () => {
    serve();
    const saved = [
      {
        configId: "c9",
        question: QUESTION.question,
        answerType: "yes_no",
        labels: ["Yes", "No"],
        reads: "transcript",
      },
    ];
    await startAsk({
      projectId: "p1",
      unit: "sessions",
      existingItems: saved,
      items: [{ id: "s1" }],
    });
    expect(postBody("/template")).toBeUndefined();
    expect(postBody("/task").evals).toEqual(["c9"]);
    expect(getCalls("/configs")).toHaveLength(0);
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
    expect(historyRun(run("k1"), configsById)).toEqual({
      taskId: "k1",
      name: "Instant eval · Refund promised?",
      status: "completed",
      createdAt: "2026-10-15T10:00:00Z",
      unit: "sessions",
      reads: "transcript",
      rows: 6,
      items: [configsById.c1],
      counts: { c1: { Yes: 4, No: 2 } },
      answered: 6,
      pending: 0,
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
    const wrapper = ({ children }) =>
      React.createElement(QueryClientProvider, { client }, children);
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
    // No eval-task list scan, no answers read per row.
    expect(getCalls("/tasks")).toHaveLength(0);
    expect(getCalls("/answers")).toHaveLength(0);

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

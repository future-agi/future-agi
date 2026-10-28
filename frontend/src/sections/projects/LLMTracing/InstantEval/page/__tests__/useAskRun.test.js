import React from "react";
import PropTypes from "prop-types";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: http.get, post: http.post },
  endpoints: {
    project: {
      instantEvalSummary: () => "/summary",
      instantEvalRows: () => "/rows",
      instantEvalControl: () => "/control",
    },
  },
}));
vi.mock("../../useInstantEval", async (importOriginal) => ({
  ...(await importOriginal()),
  useInstantEvals: () => ({ all: [], items: [], isLoading: false }),
}));

import {
  SLOWER_POLL_MS,
  SLOW_POLL_MS,
  SUMMARY_POLL_MS,
  livePollMs,
  useAskRun,
} from "../useAskRun";

const CONFIG = "config-1";
const QUESTION = "Did the agent resolve the customer's issue?";
const ok = (result) => ({
  status: 200,
  headers: {},
  data: { status: true, result },
});

const summary = ({
  status = "running",
  total = 3,
  answered = 0,
  failed = 0,
} = {}) => ({
  status,
  unit: "sessions",
  preparing: false,
  total,
  answered,
  failed,
  pending: total - answered - failed,
  not_read: 0,
  started_at: "2026-10-15T10:00:00Z",
  finished_at: status === "completed" ? "2026-10-15T10:00:30Z" : null,
  rate_per_min: 120,
  eta_s: 30,
  created_by: { id: "u1", name: "Nikhil", email: "n@example.com" },
  credits_estimate: 0.18,
  sample: {
    population: total,
    population_is_lower_bound: false,
    requested: total,
    read: total,
    is_sample: false,
    can_extend: false,
    next_size: 0,
  },
  window: { from: null, to: null },
  scope: { filters: [], sample_size: null, ids: null },
  questions: [
    {
      config_id: CONFIG,
      question: QUESTION,
      answer_type: "yes_no",
      labels: ["Yes", "No"],
    },
  ],
  counts: { [CONFIG]: { Yes: answered, No: 0, __error__: failed } },
  unsure: {},
  timeline: { bin_ms: 3600000, tz: "UTC", from: 0, to: 3600000, bins: [] },
  groups: {},
  cross: {},
});

const row = (id, answer) => ({
  id,
  time: "2026-10-13T14:10:00Z",
  user_id: "cust_ada",
  excerpt: "Where is my order?",
  facets: { model: "gpt-4o-mini", turns: 2, duration_ms: 30000, error: false },
  answers: { [CONFIG]: { answer, confidence: 0.9, status: "completed" } },
});

const wrapper = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) =>
    React.createElement(QueryClientProvider, { client }, children);
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

// Advance the fake clock and let the polls settle.
const tick = (ms) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

const calls = (url) => http.get.mock.calls.filter(([u]) => u === url);

describe("useAskRun", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.setSystemTime(new Date("2026-10-15T10:00:00Z"));
    http.get.mockReset();
    http.post.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the questions while the run is being created, reading nothing", () => {
    const { result } = renderHook(
      () =>
        useAskRun({
          projectId: "p1",
          taskId: null,
          unit: "sessions",
          questions: [
            { question: QUESTION, answerType: "yes_no", labels: ["Yes", "No"] },
          ],
          startedAt: "2026-10-15T10:00:00Z",
        }),
      { wrapper: wrapper() },
    );
    expect(http.get).not.toHaveBeenCalled();
    expect(result.current).toMatchObject({
      status: "loading",
      total: 0,
      questions: [{ configId: "", question: QUESTION, answerType: "yes_no" }],
    });
  });

  it("polls the summary every 2 s while Jev reads, then stops", async () => {
    let answered = 0;
    http.get.mockImplementation((url) => {
      if (url === "/summary")
        return Promise.resolve(
          ok(
            summary({
              status: answered >= 3 ? "completed" : "running",
              answered,
            }),
          ),
        );
      return Promise.resolve(
        ok({
          rows: ["s1", "s2", "s3"].map((id, i) =>
            row(id, i < answered ? "Yes" : null),
          ),
          next_cursor: null,
          total: 3,
        }),
      );
    });
    const { result } = renderHook(
      () =>
        useAskRun({
          projectId: "p1",
          taskId: "task-1",
          unit: "sessions",
          tz: "UTC",
        }),
      { wrapper: wrapper() },
    );
    await tick(0);
    await tick(0);
    expect(result.current).toMatchObject({
      status: "running",
      total: 3,
      answered: 0,
      rate: 120,
      eta: 30,
      questions: [
        { configId: CONFIG, question: QUESTION, answerType: "yes_no" },
      ],
    });
    // A run of a few rows also reads them, for the squares.
    expect(calls("/rows")).toHaveLength(1);
    expect(calls("/rows")[0][1].params).toMatchObject({
      compact: true,
      page_size: 1500,
      tz: "UTC",
    });
    expect(result.current.records.map((r) => r.id)).toEqual(["s1", "s2", "s3"]);

    answered = 2;
    await tick(SUMMARY_POLL_MS);
    expect(result.current.answered).toBe(2);
    // The rows are read again because answers landed.
    expect(calls("/rows").length).toBeGreaterThanOrEqual(2);

    answered = 3;
    await tick(SUMMARY_POLL_MS);
    expect(result.current.status).toBe("done");
    const polls = calls("/summary").length;
    await tick(SUMMARY_POLL_MS * 3);
    expect(calls("/summary")).toHaveLength(polls);
    expect(result.current.elapsedMs).toBe(30000);
  });

  it("stops polling a summary that keeps failing, until a retry", async () => {
    http.get.mockImplementation(() =>
      Promise.reject(
        Object.assign(new Error("unavailable"), { statusCode: 503 }),
      ),
    );
    const { result } = renderHook(
      () =>
        useAskRun({
          projectId: "p1",
          taskId: "task-1",
          unit: "sessions",
          tz: "UTC",
        }),
      { wrapper: wrapper() },
    );
    // The first read and its two retries (1 s, then 2 s later).
    await tick(0);
    for (let i = 0; i < 6; i += 1) await tick(1_000);
    expect(result.current.status).toBe("error");
    const reads = calls("/summary").length;
    expect(reads).toBe(3);
    await tick(60_000);
    expect(calls("/summary")).toHaveLength(reads);
  });

  it("doesn't retry a summary the server refused", async () => {
    http.get.mockImplementation(() =>
      Promise.reject(Object.assign(new Error("gone"), { statusCode: 404 })),
    );
    const { result } = renderHook(
      () =>
        useAskRun({
          projectId: "p1",
          taskId: "task-1",
          unit: "sessions",
          tz: "UTC",
        }),
      { wrapper: wrapper() },
    );
    await tick(0);
    await tick(30_000);
    expect(result.current.status).toBe("error");
    expect(calls("/summary")).toHaveLength(1);
  });

  it("polls a slow server less often", () => {
    expect(livePollMs(0)).toBe(SUMMARY_POLL_MS);
    expect(livePollMs(900)).toBe(SUMMARY_POLL_MS);
    expect(livePollMs(1500)).toBe(SLOW_POLL_MS);
    expect(livePollMs(7000)).toBe(SLOWER_POLL_MS);
  });

  it("reads no rows for a run too big to draw one square each", async () => {
    http.get.mockResolvedValue(
      ok(summary({ status: "running", total: 104233, answered: 5000 })),
    );
    const { result } = renderHook(
      () => useAskRun({ projectId: "p1", taskId: "task-1", unit: "sessions" }),
      { wrapper: wrapper() },
    );
    await tick(0);
    expect(result.current.total).toBe(104233);
    expect(result.current.records).toBeNull();
    expect(calls("/rows")).toHaveLength(0);
  });

  it("reads the squares again once a retry ends where the run ended before", async () => {
    // Five rows that all failed (a rejected key), retried and answered: the
    // run settles at five both times.
    let phase = "failed";
    const ids = ["s1", "s2", "s3", "s4", "s5"];
    http.get.mockImplementation((url) => {
      if (url === "/summary")
        return Promise.resolve(
          ok(
            phase === "failed"
              ? summary({ status: "completed", total: 5, failed: 5 })
              : phase === "retrying"
                ? summary({ status: "running", total: 5 })
                : summary({ status: "completed", total: 5, answered: 5 }),
          ),
        );
      return Promise.resolve(
        ok({
          rows: ids.map((id) =>
            phase === "failed"
              ? {
                  ...row(id, null),
                  answers: { [CONFIG]: { answer: null, status: "failed" } },
                }
              : row(id, phase === "answered" ? "Yes" : null),
          ),
          next_cursor: null,
          total: 5,
        }),
      );
    });
    http.post.mockImplementation(() => {
      phase = "retrying";
      return Promise.resolve(ok({ status: "running", message: "Retrying." }));
    });
    const { result } = renderHook(
      () =>
        useAskRun({
          projectId: "p1",
          taskId: "task-1",
          unit: "sessions",
          tz: "UTC",
        }),
      { wrapper: wrapper() },
    );
    await tick(0);
    await tick(0);
    expect(result.current.status).toBe("done");
    const statuses = () =>
      result.current.records.map((r) => r.answers[CONFIG].status);
    expect(statuses()).toEqual(Array(5).fill("failed"));

    await act(async () => {
      await result.current.control("retry_failed");
    });
    await tick(0);
    expect(result.current.status).toBe("running");

    phase = "answered";
    await tick(SUMMARY_POLL_MS);
    await tick(0);
    expect(result.current.status).toBe("done");
    expect(result.current.answered).toBe(5);
    // The squares agree with the counts, not the failures before the retry.
    expect(result.current.records.map((r) => r.answers[CONFIG].answer)).toEqual(
      Array(5).fill("Yes"),
    );
  });

  it("reads the summary now without cancelling a read in flight", async () => {
    let reply;
    http.get.mockResolvedValueOnce(
      ok(summary({ status: "running", total: 9000, answered: 10 })),
    );
    const { result } = renderHook(
      () => useAskRun({ projectId: "p1", taskId: "task-1", unit: "sessions" }),
      { wrapper: wrapper() },
    );
    await tick(0);
    expect(calls("/summary")).toHaveLength(1);
    // A slow read is in flight; readNow joins it rather than starting over.
    http.get.mockImplementation(
      () =>
        new Promise((resolve) => {
          reply = resolve;
        }),
    );
    await tick(SUMMARY_POLL_MS);
    expect(calls("/summary")).toHaveLength(2);
    const readNow = result.current.readNow;
    await act(async () => {
      readNow();
    });
    expect(calls("/summary")).toHaveLength(2);
    expect(calls("/summary")[1][1].signal.aborted).toBe(false);
    await act(async () => {
      reply(ok(summary({ status: "running", total: 9000, answered: 40 })));
    });
    await tick(0);
    expect(result.current.answered).toBe(40);
    // The same function from one render to the next.
    expect(result.current.readNow).toBe(readNow);
  });

  it("pauses through the control endpoint and reads the summary again", async () => {
    let status = "running";
    http.get.mockImplementation(() =>
      Promise.resolve(ok(summary({ status, total: 9000, answered: 10 }))),
    );
    http.post.mockImplementation(() => {
      status = "paused";
      return Promise.resolve(ok({ status: "paused", message: "Paused." }));
    });
    const { result } = renderHook(
      () => useAskRun({ projectId: "p1", taskId: "task-1", unit: "sessions" }),
      { wrapper: wrapper() },
    );
    await tick(0);
    await act(async () => {
      await result.current.control("pause");
    });
    await tick(0);
    expect(http.post).toHaveBeenCalledWith("/control", {
      project_id: "p1",
      eval_task_id: "task-1",
      action: "pause",
    });
    expect(result.current.status).toBe("paused");
  });
});

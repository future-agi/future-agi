import React from "react";
import PropTypes from "prop-types";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: http.get },
  endpoints: {
    project: {
      instantEvalRuns: () => "/runs",
      instantEvalAnswers: () => "/answers",
    },
  },
}));
vi.mock("../../useInstantEval", async (importOriginal) => ({
  ...(await importOriginal()),
  useInstantEvals: () => ({ all: [], items: [], isLoading: false }),
}));

import { ANSWERS_POLL_MS, useAskRun } from "../useAskRun";

const CONFIG = "config-1";
const QUESTION = "Did the agent resolve the customer's issue?";
const SEEDS = [
  { id: "s1", time: "2026-10-13T14:10:00Z", user_id: "cust_ada" },
  { id: "s2", time: "2026-10-13T15:20:00Z", user_id: "cust_ben" },
  { id: "s3", time: "2026-10-14T09:00:00Z", user_id: "cust_kai" },
];
const ok = (result) => ({ data: { status: true, result } });

const taskRun = (status = "running") => ({
  task_id: "task-1",
  name: `Instant eval · ${QUESTION}`,
  created_at: "2026-10-15T10:00:00Z",
  status,
  run_type: "historical",
  unit: "sessions",
  reads: "transcript",
  rows: 3,
  config_ids: [CONFIG],
  questions: [QUESTION],
  counts: {},
  answered: 0,
  pending: 3,
  finished_at: null,
});

const answerRow = (seed, answer) => ({
  id: seed.id,
  time: seed.time,
  user_id: seed.user_id,
  label: seed.id,
  excerpt: `Where is my order? (${seed.id})`,
  facets: { model: "gpt-4o-mini", turns: 2, duration_ms: 30000, error: false },
  answers: answer
    ? { [CONFIG]: { answer, confidence: 0.9, explanation: "" } }
    : { [CONFIG]: { answer: null, status: "running" } },
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

describe("useAskRun", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.setSystemTime(new Date("2026-10-15T10:00:00Z"));
    http.get.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows every row as not yet read while the run is being created", async () => {
    const { result } = renderHook(
      () =>
        useAskRun({
          projectId: "p1",
          taskId: null,
          unit: "sessions",
          seedItems: SEEDS,
          questions: [
            { question: QUESTION, answerType: "yes_no", labels: ["Yes", "No"] },
          ],
          startedAt: "2026-10-15T10:00:00Z",
        }),
      { wrapper: wrapper() },
    );
    expect(http.get).not.toHaveBeenCalled();
    expect(result.current).toMatchObject({
      status: "running",
      answered: 0,
      total: 3,
      questions: [{ configId: "", question: QUESTION, answerType: "yes_no" }],
    });
    // Newest first, each with no answers yet.
    expect(result.current.records.map((r) => r.id)).toEqual(["s3", "s2", "s1"]);
    expect(
      result.current.records.every((r) => !Object.keys(r.answers).length),
    ).toBe(true);
    expect(result.current.window).toEqual([
      "2026-10-13T14:10:00.000Z",
      "2026-10-14T09:00:00.000Z",
    ]);
  });

  it("polls the answers every 1.5 s while Jev reads, then stops", async () => {
    let answered = 0;
    http.get.mockImplementation((url) => {
      if (url === "/runs")
        return Promise.resolve(
          ok({
            runs: [taskRun(answered >= 3 ? "completed" : "running")],
            has_more: false,
          }),
        );
      const rows = SEEDS.slice(0, answered).map((seed, index) =>
        answerRow(seed, index === 0 ? "No" : "Yes"),
      );
      // The next row is being read.
      if (answered < 3) rows.push(answerRow(SEEDS[answered], null));
      return Promise.resolve(ok({ rows }));
    });
    const answerCalls = () =>
      http.get.mock.calls.filter(([url]) => url === "/answers").length;

    const { result } = renderHook(
      () =>
        useAskRun({
          projectId: "p1",
          taskId: "task-1",
          unit: "sessions",
          seedItems: SEEDS,
          startedAt: "2026-10-15T10:00:00Z",
        }),
      { wrapper: wrapper() },
    );
    await tick(0);
    await tick(0);
    expect(result.current.questions).toEqual([
      expect.objectContaining({ configId: CONFIG, question: QUESTION }),
    ]);
    expect(answerCalls()).toBe(1);
    expect(result.current).toMatchObject({
      status: "running",
      answered: 0,
      total: 3,
    });
    // The row being read is in flight; the others not read yet.
    const s1 = result.current.records.find((r) => r.id === "s1");
    expect(s1.answers[CONFIG]).toMatchObject({
      answer: null,
      status: "running",
    });
    expect(s1.facets.model).toBe("gpt-4o-mini");

    answered = 2;
    await tick(ANSWERS_POLL_MS);
    expect(answerCalls()).toBe(2);
    expect(result.current.answered).toBe(2);
    // The clock ticks each second, counted from the launch.
    expect(result.current.elapsedMs).toBe(1000);

    answered = 3;
    await tick(ANSWERS_POLL_MS);
    expect(answerCalls()).toBe(3);
    expect(result.current).toMatchObject({
      status: "done",
      answered: 3,
      total: 3,
    });
    const doneElapsed = result.current.elapsedMs;

    // Done: no more answer reads, and the time stops.
    await tick(ANSWERS_POLL_MS * 4);
    expect(answerCalls()).toBe(3);
    expect(result.current.elapsedMs).toBe(doneElapsed);
    expect(
      result.current.records.find((r) => r.id === "s1").answers[CONFIG].answer,
    ).toBe("No");
  });

  it("ends with what the task left when it stops short", async () => {
    http.get.mockImplementation((url) =>
      Promise.resolve(
        url === "/runs"
          ? ok({ runs: [taskRun("failed")], has_more: false })
          : ok({ rows: [answerRow(SEEDS[0], "Yes")] }),
      ),
    );
    const { result } = renderHook(
      () =>
        useAskRun({
          projectId: "p1",
          taskId: "task-1",
          unit: "sessions",
          seedItems: SEEDS,
        }),
      { wrapper: wrapper() },
    );
    await tick(0);
    await tick(0);
    await tick(ANSWERS_POLL_MS);
    expect(result.current).toMatchObject({
      status: "done",
      answered: 1,
      total: 3,
    });
  });
});

import { describe, expect, it, vi } from "vitest";
import {
  EVAL_TASK_DETAIL_REQUEST_TIMEOUT_MS,
  readEvalTaskDetail,
} from "./task_detail_read";

const validResponse = (overrides = {}) => ({
  data: {
    status: true,
    result: {
      id: "task-1",
      name: "Task one",
      project_id: "project-1",
      project_name: "Project one",
      status: "completed",
      filters_applied: {},
      evals_applied: [],
      spans_limit: 100,
      sampling_rate: 100,
      run_type: "continuous",
      row_type: "spans",
      ...overrides,
    },
  },
});

describe("readEvalTaskDetail", () => {
  it("passes one bounded signal and keeps the raw axios cache shape", async () => {
    const response = validResponse();
    const requestTask = vi.fn(({ signal, timeout }) => {
      expect(signal).toBeInstanceOf(AbortSignal);
      expect(timeout).toBe(EVAL_TASK_DETAIL_REQUEST_TIMEOUT_MS);
      return Promise.resolve(response);
    });

    await expect(readEvalTaskDetail(requestTask)).resolves.toBe(response);
    expect(requestTask).toHaveBeenCalledOnce();
  });

  it("accepts nullable fields that remain valid on legacy task rows", async () => {
    const response = validResponse({
      name: null,
      status: null,
      filters_applied: null,
      spans_limit: null,
      sampling_rate: null,
      run_type: null,
    });

    await expect(
      readEvalTaskDetail(() => Promise.resolve(response)),
    ).resolves.toBe(response);
  });

  it.each([
    [{ status: true, result: [] }, "non-object result"],
    [validResponse({ id: null }).data, "missing task id"],
    [validResponse({ filters_applied: [] }).data, "invalid filters"],
    [validResponse({ evals_applied: null }).data, "invalid eval list"],
    [validResponse({ sampling_rate: "100" }).data, "invalid sampling rate"],
  ])("fails closed for %s (%s)", async (data) => {
    await expect(
      readEvalTaskDetail(() => Promise.resolve({ data })),
    ).rejects.toMatchObject({ code: "eval_task_detail_invalid_response" });
  });

  it("aborts a stalled request at the configured deadline", async () => {
    vi.useFakeTimers();
    let requestSignal;
    const request = readEvalTaskDetail(({ signal }) => {
      requestSignal = signal;
      return new Promise(() => {});
    });
    const rejection = expect(request).rejects.toMatchObject({
      code: "aggregation_request_timeout",
    });

    await vi.advanceTimersByTimeAsync(EVAL_TASK_DETAIL_REQUEST_TIMEOUT_MS);

    await rejection;
    expect(requestSignal.aborted).toBe(true);
    vi.useRealTimers();
  });
});

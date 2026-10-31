import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import useSQLExecution from "./useSQLExecution";
import { sqlApi } from "./api";

vi.mock("./api", () => ({
  errorMessage: (e) => e.message,
  sqlApi: { run: vi.fn(), runSaved: vi.fn(), result: vi.fn(), cancel: vi.fn() },
}));

afterEach(() => vi.clearAllMocks());

describe("SQL execution lifecycle", () => {
  it("does not publish an old project's delayed response after navigation", async () => {
    let resolve;
    sqlApi.run.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const { result, rerender } = renderHook(
      ({ project }) => useSQLExecution(project),
      { initialProps: { project: "a" } },
    );
    let pending;
    act(() => {
      pending = result.current.run({ sql: "SELECT * FROM spans" });
    });
    rerender({ project: "b" });
    await act(async () => {
      resolve({
        id: "old",
        state: "succeeded",
        result: { rows: [["private"]] },
      });
      await pending;
    });
    expect(result.current.job).toBeNull();
    expect(result.current.busy).toBeFalsy();
  });
  it("polls the owner's bounded async job and cancels through the same project", async () => {
    sqlApi.run.mockResolvedValue({ id: "job", state: "running" });
    sqlApi.result.mockResolvedValue({
      id: "job",
      state: "succeeded",
      result: { rows: [[1]] },
    });
    const { result } = renderHook(() => useSQLExecution("project"));
    await act(async () =>
      result.current.run({ sql: "SELECT count() FROM spans" }),
    );
    expect(sqlApi.run).toHaveBeenCalledWith(
      {
        project_id: "project",
        sql: "SELECT count() FROM spans",
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    await waitFor(() => expect(result.current.job.state).toBe("succeeded"));
    expect(sqlApi.result.mock.calls[0][0]).toBe("project");
    expect(result.current.job.result.rows).toEqual([[1]]);
  });
  it("keeps polling when active-job cancellation fails", async () => {
    let resolveResult;
    sqlApi.run.mockResolvedValue({ id: "job", state: "running" });
    sqlApi.result.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveResult = resolve;
        }),
    );
    sqlApi.cancel.mockRejectedValue(new Error("temporary cancellation failure"));
    const { result } = renderHook(() => useSQLExecution("project"));

    await act(async () => {
      await result.current.run({ sql: "SELECT count() FROM spans" });
    });
    await waitFor(() => expect(sqlApi.result).toHaveBeenCalledTimes(1));

    await act(async () => {
      await result.current.cancel();
    });
    expect(result.current.error).toBe("temporary cancellation failure");

    await act(async () => {
      resolveResult({
        id: "job",
        state: "succeeded",
        result: { rows: [[1]] },
      });
    });
    await waitFor(() => expect(result.current.job.state).toBe("succeeded"));
    expect(result.current.busy).toBe(false);
  });

  it("surfaces a submission failure without fabricating rows", async () => {
    sqlApi.run.mockRejectedValue(new Error("Project not available"));
    const { result } = renderHook(() => useSQLExecution("project"));
    await act(async () => result.current.run({ sql: "SELECT * FROM spans" }));
    expect(result.current.error).toBe("Project not available");
    expect(result.current.job).toBeNull();
  });
  it("submits a saved reference, not SQL copied from a dashboard", async () => {
    sqlApi.runSaved.mockResolvedValue({ id: "job", state: "succeeded" });
    const { result } = renderHook(() => useSQLExecution("project"));
    await act(async () =>
      result.current.run({ saved_query_id: "saved" }, true),
    );
    expect(sqlApi.runSaved).toHaveBeenCalledWith(
      {
        project_id: "project",
        saved_query_id: "saved",
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(sqlApi.run).not.toHaveBeenCalled();
  });
});

it("stops a waiting dashboard submission when cancelled", async () => {
  sqlApi.runSaved.mockRejectedValue(
    Object.assign(new Error("busy"), { statusCode: 429 }),
  );
  const { result } = renderHook(() =>
    useSQLExecution("project", { retryAdmission: true }),
  );
  let pending;
  act(() => {
    pending = result.current.run({ saved_query_id: "waiting" }, true);
  });
  await waitFor(() => expect(result.current.waitingForAdmission).toBe(true));
  await act(async () => {
    await result.current.cancel();
    await pending;
  });
  expect(result.current.busy).toBeFalsy();
  expect(result.current.waitingForAdmission).toBe(false);
  expect(sqlApi.runSaved).toHaveBeenCalledTimes(1);
});

it("bounds admission retries instead of retrying permanently", async () => {
  sqlApi.runSaved.mockRejectedValue(
    Object.assign(new Error("busy"), { statusCode: 429 }),
  );
  const { result } = renderHook(() =>
    useSQLExecution("project", {
      retryAdmission: true,
      admissionTimeoutMs: 10,
    }),
  );
  await act(async () => {
    await result.current.run({ saved_query_id: "waiting" }, true);
  });
  expect(result.current.busy).toBeFalsy();
  expect(result.current.error).toBe("busy");
  expect(sqlApi.runSaved).toHaveBeenCalledTimes(1);
});

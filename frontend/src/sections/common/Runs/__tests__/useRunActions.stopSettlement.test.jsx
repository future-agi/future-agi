import React from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetRunControlForTest } from "src/sections/common/ExperimentRunControl/runControlAvailability";
import { useRunActionConfirmStore } from "src/sections/develop-detail/Experiment/runActionConfirm";
import useRunActions from "../useRunActions";

const api = vi.hoisted(() => ({
  stop: vi.fn(),
  resume: vi.fn(),
  detail: vi.fn(),
  notify: vi.fn(),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: api.notify }));
vi.mock("src/api/develop/experiment-run-actions", () => ({
  stopRuns: api.stop,
  resumeRuns: api.resume,
  rerunCells: vi.fn(),
  rerunExperiments: vi.fn(),
  uploadRunResults: vi.fn(),
}));
vi.mock("src/api/develop/runs", async (original) => ({
  ...(await original()),
  fetchExperimentDetail: api.detail,
}));

const run = {
  run_id: "run-one",
  experiment_id: "experiment-one",
  label: "#1A",
  status: "running",
  progress: { done: 1, total: 5, errors: 0 },
};
const envelope = (result) => ({ data: { status: true, result } });
const acknowledgement = (status) =>
  envelope({ runs: [{ run_id: run.run_id, name: "Prompt", status }] });
const detail = (status, done) =>
  envelope({
    runs: [
      { ...run, status, completed_at: null, progress: { done, total: 5 } },
    ],
    runs_total: 1,
    runs_has_more: false,
  });
let client;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
const messages = () =>
  api.notify.mock.calls.map(([text, options]) => [
    text,
    options.variant,
    Boolean(options.undo),
  ]);
const requested = [
  "Stop requested for #1A. Waiting for stopped state and kept-row count.",
  "info",
  false,
];

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  resetRunControlForTest();
  useRunActionConfirmStore.setState({ hosts: [], request: null });
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  api.stop.mockResolvedValue(acknowledgement("STOPPING"));
  api.resume.mockResolvedValue(acknowledgement("RUNNING"));
  api.detail.mockResolvedValue(detail("running", 2));
});
afterEach(() => {
  cleanup();
  client.clear();
  resetRunControlForTest();
  vi.useRealTimers();
});

describe.each(["stop", "stopInList"])("%s settlement", (action) => {
  it("waits for settled counts before offering Undo", async () => {
    const { result } = renderHook(() => useRunActions(run), { wrapper });
    let pending;
    await act(async () => {
      pending = result.current[action]();
    });
    expect(api.stop.mock.calls).toEqual([[run.experiment_id, [run.run_id]]]);
    expect(messages()).toEqual([requested]);
    expect(api.detail.mock.calls).toEqual([[run.experiment_id]]);
    api.detail.mockResolvedValue(detail("cancelled", 3));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
      await pending;
    });
    expect(messages()).toEqual([
      requested,
      ["Stopped #1A · 3 of 5 rows kept", "success", true],
    ]);
    api.detail.mockResolvedValue(detail("running", 3));
    await act(async () => api.notify.mock.calls[1][1].undo.onUndo());
    expect(api.resume.mock.calls).toEqual([[run.experiment_id, [run.run_id]]]);
  });

  it.each([false, true])(
    "reads settlement once for an in-progress response with counts=%s",
    async (withCounts) => {
      let settle;
      api.detail.mockReturnValue(
        new Promise((resolve) => {
          settle = resolve;
        }),
      );
      api.stop.mockResolvedValue(
        envelope({
          runs: [
            {
              run_id: run.run_id,
              status: "STOPPING",
              ...(withCounts && { done: 1, total: 5 }),
            },
          ],
        }),
      );
      const { result } = renderHook(() => useRunActions(run), { wrapper });
      let pending;
      await act(async () => {
        pending = result.current[action]();
      });
      expect(api.stop.mock.calls).toEqual([[run.experiment_id, [run.run_id]]]);
      expect(api.detail.mock.calls).toEqual([[run.experiment_id]]);
      expect(messages()).toEqual([requested]);
      expect(result.current.pending).toBe(true);
      await act(async () => {
        settle(detail("cancelled", 3));
        await pending;
      });
      expect(api.detail.mock.calls).toEqual([[run.experiment_id]]);
      expect(messages()).toEqual([
        requested,
        ["Stopped #1A · 3 of 5 rows kept", "success", true],
      ]);
      expect(result.current.pending).toBe(false);
    },
  );

  it.each(["STOPPED", "stopped", "Cancelled", "cancelled", "PAUSED", "paused"])(
    "settles %s from response progress without a read",
    async (status) => {
      api.stop.mockResolvedValue(
        envelope({
          runs: [
            { run_id: run.run_id, status, progress: { done: 3, total: 7 } },
          ],
        }),
      );
      const { result } = renderHook(() => useRunActions(run), { wrapper });
      await act(() => result.current[action]());
      expect(api.stop.mock.calls).toEqual([[run.experiment_id, [run.run_id]]]);
      expect(api.detail.mock.calls).toEqual([]);
      expect(messages()).toEqual([
        ["Stopped #1A · 3 of 7 rows kept", "success", true],
      ]);
      expect(result.current.pending).toBe(false);
      await act(async () => api.notify.mock.calls[0][1].undo.onUndo());
      expect(api.resume.mock.calls).toEqual([
        [run.experiment_id, [run.run_id]],
      ]);
    },
  );

  it.each([
    [{ status: "STOPPED", done: 3, total: 7 }, "3 of 7"],
    [{ done: 4, total: 40 }, "4 of 40"],
    [{ done: 4 }, "4 of 5"],
    [{ done: 0 }, "0 of 5"],
  ])(
    "retains the settled count response %j without a read",
    async (ack, kept) => {
      api.stop.mockResolvedValue(
        envelope({ runs: [{ run_id: run.run_id, ...ack }] }),
      );
      const { result } = renderHook(() => useRunActions(run), { wrapper });
      await act(() => result.current[action]());
      expect(api.stop.mock.calls).toEqual([[run.experiment_id, [run.run_id]]]);
      expect(api.detail.mock.calls).toEqual([]);
      expect(messages()).toEqual([
        [`Stopped #1A · ${kept} rows kept`, "success", true],
      ]);
    },
  );

  it.each(["STOPPED", "PAUSED"])(
    "does not invent final counts for %s or read them back",
    async (status) => {
      api.stop.mockResolvedValue(acknowledgement(status));
      const { result } = renderHook(() => useRunActions(run), { wrapper });
      await act(() => result.current[action]());
      expect(api.stop.mock.calls).toEqual([[run.experiment_id, [run.run_id]]]);
      expect(api.detail.mock.calls).toEqual([]);
      expect(messages()).toEqual([
        ["Stopped #1A · kept-row count unavailable", "success", false],
      ]);
    },
  );

  it.each(["COMPLETED", "completed", "FAILED", "PARTIAL_RUN"])(
    "does not claim a stop for an already ended run with %s",
    async (status) => {
      api.stop.mockResolvedValue(acknowledgement(status));
      const { result } = renderHook(() => useRunActions(run), { wrapper });
      await act(() => result.current[action]());
      expect(messages()).toEqual([
        ["This run is no longer running; nothing was stopped.", "info", false],
      ]);
      expect(api.detail.mock.calls).toEqual([]);
    },
  );

  it.each([
    { runs: [] },
    { runs: [{ run_id: "sibling", status: "STOPPED", done: 3, total: 5 }] },
    { runs: [{ run_id: run.run_id }] },
    { runs: [{ run_id: run.run_id, status: "unknown", done: 3, total: 5 }] },
  ])(
    "does not infer settlement from an unconfirmed response %j",
    async (response) => {
      api.stop.mockResolvedValue(envelope(response));
      const { result } = renderHook(() => useRunActions(run), { wrapper });
      await act(() => result.current[action]());
      expect(api.detail.mock.calls).toEqual([]);
      expect(messages()).toEqual([
        [
          "Stop requested for #1A. Could not confirm its stopped state; check the run before resuming.",
          "info",
          false,
        ],
      ]);
    },
  );

  it("tracks the original run after the menu unmounts", async () => {
    const { result, unmount } = renderHook(() => useRunActions(run), {
      wrapper,
    });
    let pending;
    await act(async () => {
      pending = result.current[action]();
    });
    unmount();
    api.detail.mockResolvedValue(detail("cancelled", 4));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
      await pending;
    });
    expect(messages()).toEqual([
      requested,
      ["Stopped #1A · 4 of 5 rows kept", "success", true],
    ]);
  });

  it("does not substitute a stopped sibling", async () => {
    api.detail.mockResolvedValue(
      envelope({
        runs: [
          { ...run, status: "running" },
          { ...run, run_id: "sibling", status: "cancelled" },
        ],
      }),
    );
    const { result } = renderHook(() => useRunActions(run), { wrapper });
    let pending;
    await act(async () => {
      pending = result.current[action]();
    });
    expect(messages()).toEqual([requested]);
    api.detail.mockResolvedValue(detail("completed", 5));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
      await pending;
    });
    expect(messages()).toEqual([
      requested,
      ["This run is no longer running; nothing was stopped.", "info", false],
    ]);
  });

  it("leaves a slow stop unresolved without offering Undo", async () => {
    const { result } = renderHook(() => useRunActions(run), { wrapper });
    let pending;
    await act(async () => {
      pending = result.current[action]();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
      await pending;
    });
    expect(api.detail.mock.calls.length).toBe(60);
    expect(messages()).toEqual([
      requested,
      [
        "Stop is still pending for #1A. Check the run before resuming.",
        "info",
        false,
      ],
    ]);
  });

  it("reports unreadable settlement without a final count", async () => {
    api.detail.mockRejectedValue(new Error("read unavailable"));
    const { result } = renderHook(() => useRunActions(run), { wrapper });
    await act(() => result.current[action]());
    expect(messages()).toEqual([
      requested,
      [
        "Stop requested for #1A. Could not confirm its stopped state; check the run before resuming.",
        "info",
        false,
      ],
    ]);
  });
});

describe.each(["resume", "undo"])("%s persistence", (action) => {
  it.each(["running", "completed"])(
    "refreshes the selected run after detail advances to %s",
    async (status) => {
      const stopped = detail("cancelled", 3);
      const settled = detail(status, status === "completed" ? 5 : 3);
      const otherKey = ["experiment", "other-experiment"];
      const other = envelope({ runs: [{ ...run, run_id: "other-run" }] });
      client.setQueryData(["experiment", run.experiment_id], stopped);
      client.setQueryData(otherKey, other);
      api.detail.mockResolvedValue(stopped);
      api.stop.mockResolvedValue(
        envelope({
          runs: [{ run_id: run.run_id, status: "STOPPED", done: 3, total: 5 }],
        }),
      );
      const { result, unmount } = renderHook(() => useRunActions(run), {
        wrapper,
      });
      let resume = () => result.current.resume();
      if (action === "undo") {
        await act(() => result.current.stop());
        resume = api.notify.mock.calls[0][1].undo.onUndo;
        api.notify.mockClear();
      }
      let pending;
      await act(async () => {
        pending = resume();
      });
      expect(api.resume.mock.calls).toEqual([
        [run.experiment_id, [run.run_id]],
      ]);
      expect(api.detail.mock.calls).toEqual([[run.experiment_id]]);
      expect(client.getQueryData(["experiment", run.experiment_id])).toEqual(
        stopped,
      );
      expect(messages()).toEqual([]);
      // Closing the originating menu must not strand its stopped cache.
      unmount();
      api.detail.mockResolvedValue(settled);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
        await pending;
      });
      expect(api.detail.mock.calls).toEqual([
        [run.experiment_id],
        [run.experiment_id],
      ]);
      expect(client.getQueryData(["experiment", run.experiment_id])).toEqual(
        settled,
      );
      expect(client.getQueryData(otherKey)).toBe(other);
      expect(api.notify.mock.calls.map(([text]) => text)).toEqual([
        action === "undo" ? "#1A running again" : "#1A resumed · 4 rows left",
      ]);
      expect(api.resume.mock.calls).toEqual([
        [run.experiment_id, [run.run_id]],
      ]);
    },
  );
});

it("leaves an unconfirmed resume stopped without offering Undo", async () => {
  const stopped = detail("cancelled", 3);
  api.detail.mockResolvedValue(stopped);
  client.setQueryData(["experiment", run.experiment_id], stopped);
  const { result } = renderHook(() => useRunActions(run), { wrapper });
  let pending;
  await act(async () => {
    pending = result.current.resume();
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(59000);
    await pending;
  });
  expect(api.detail.mock.calls).toHaveLength(60);
  expect(api.resume.mock.calls).toEqual([[run.experiment_id, [run.run_id]]]);
  expect(client.getQueryData(["experiment", run.experiment_id])).toEqual(
    stopped,
  );
  expect(messages()).toEqual([
    [
      "Resume requested for #1A. Could not confirm its current state; refresh the run.",
      "info",
      false,
    ],
  ]);
  expect(result.current.pending).toBe(false);
});

it("does not confirm resume from an active sibling", async () => {
  api.detail.mockResolvedValue(
    envelope({ runs: [{ ...run, run_id: "sibling", status: "running" }] }),
  );
  const { result } = renderHook(() => useRunActions(run), { wrapper });
  await act(() => result.current.resume());
  expect(api.detail.mock.calls).toEqual([[run.experiment_id]]);
  expect(messages()).toEqual([
    [
      "Resume requested for #1A. Could not confirm its current state; refresh the run.",
      "info",
      false,
    ],
  ]);
});

it("waits for settlement when Undo requests a stop after Resume", async () => {
  const { result } = renderHook(() => useRunActions(run), { wrapper });
  await act(() => result.current.resume());
  const undo = api.notify.mock.calls[0][1].undo;
  api.notify.mockClear();
  let pending;
  await act(async () => {
    pending = undo.onUndo();
  });
  expect(messages()).toEqual([requested]);
  api.detail.mockResolvedValue(detail("cancelled", 3));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
    await pending;
  });
  expect(messages()).toEqual([
    requested,
    ["Stopped #1A · 3 of 5 rows kept", "success", true],
  ]);
});

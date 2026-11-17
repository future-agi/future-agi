import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import rowSource from "src/sections/experiment-detail/ExperimentRow.jsx?raw";
import { isExperimentPending } from "src/api/develop/experiment-status-refresh";
import { resetAllStores } from "src/utils/zustand";
import {
  sendRerunSubmission,
  useRerunStarts,
  useRerunSubmission,
  useRerunSubmissionHeld,
} from "../rerunSubmission";

afterEach(() => {
  cleanup();
  resetAllStores();
});

describe("Rerun with changes while work is active", () => {
  it("uses the classic pending-status rule and active run evidence", () => {
    expect(rowSource).toContain(
      'from "src/api/develop/experiment-status-refresh"',
    );
    expect(rowSource).toMatch(
      /const rerunChangesPending =\s*!rerunChangesHeld &&\s*\(isExperimentPending\(experiment\?\.status\) \|\| activeRuns\.length > 0\)/,
    );
    expect(rowSource).toMatch(
      /<MenuItem\s+disabled=\{rerunChangesPending\}[\s\S]*?data-testid="exp-rerun-changes"/,
    );
    expect(rowSource).toMatch(
      /if \(rerunChangesPending\) return;[\s\S]*?openRerunWithChanges/,
    );
  });

  it("stays open for the experiment's submission that can be sent again", () => {
    expect(rowSource).toContain(
      'from "src/sections/common/RunSources/rerunSubmission"',
    );
    expect(rowSource).toMatch(
      /const rerunChangesHeld = useRerunSubmissionHeld\(experimentId\);/,
    );
  });

  it.each(["Queued", "Running", "queued", "running"])("blocks %s", (status) => {
    expect(isExperimentPending(status)).toBe(true);
  });

  it.each(["Completed", "Failed", "Cancelled"])("allows %s", (status) => {
    expect(isExperimentPending(status)).toBe(false);
  });
});

describe("A submission the menu keeps reachable", () => {
  const body = { append_only: true, prompt_config: [{ name: "Variant" }] };
  const watch = () =>
    renderHook(() => ({
      held: useRerunSubmissionHeld("experiment-a"),
      entry: useRerunSubmission("experiment-a"),
    }));

  it("is held while in flight and after an unconfirmed answer", async () => {
    let answer;
    const send = vi.fn(
      () =>
        new Promise((_, reject) => {
          answer = reject;
        }),
    );
    const { result } = watch();
    expect(result.current.held).toBe(false);
    act(() => sendRerunSubmission("experiment-a", { body }, { send }));
    await waitFor(() => expect(send).toHaveBeenCalledOnce());
    await waitFor(() => expect(result.current.held).toBe(true));
    await act(async () => answer({ statusCode: 503 }));
    await waitFor(() =>
      expect(result.current.entry?.status).toBe("unconfirmed"),
    );
    expect(result.current.held).toBe(true);
    const other = renderHook(() => useRerunSubmissionHeld("experiment-b"));
    expect(other.result.current).toBe(false);
  });

  it("sends only the held body again, whatever is built next", async () => {
    const send = vi
      .fn()
      .mockRejectedValueOnce({ transportCode: "ERR_NETWORK" })
      .mockResolvedValueOnce({ status: "Queued" });
    const onStarted = vi.fn();
    const { result } = watch();
    act(() => sendRerunSubmission("experiment-a", { body }, { send }));
    await waitFor(() =>
      expect(result.current.entry?.status).toBe("unconfirmed"),
    );
    act(() =>
      sendRerunSubmission(
        "experiment-a",
        { body: { append_only: true, prompt_config: [{ name: "Other" }] } },
        { send, onStarted },
      ),
    );
    await waitFor(() =>
      expect(onStarted).toHaveBeenCalledWith({ status: "Queued" }),
    );
    expect(send).toHaveBeenCalledTimes(2);
    expect(send.mock.calls[1][0]).toBe(body);
    await waitFor(() =>
      expect(result.current).toEqual({ held: false, entry: null }),
    );
  });

  it("is released by a refusal", async () => {
    const send = vi
      .fn()
      .mockRejectedValueOnce({ statusCode: 400, message: "Refused" });
    const { result } = watch();
    act(() => sendRerunSubmission("experiment-a", { body }, { send }));
    await waitFor(() => expect(result.current.entry?.status).toBe("refused"));
    expect(result.current.held).toBe(false);
    expect(result.current.entry).not.toHaveProperty("body");
  });
});

describe("A submission still in flight at logout", () => {
  const body = { append_only: true, prompt_config: [{ name: "Variant" }] };
  const sent = {
    body,
    source: { id: "config-a" },
    draft: { name: "Variant", temperature: "0.5" },
    label: "#1",
  };
  const unconfirmed = (answer) => answer.reject({ statusCode: 503 });
  const confirmed = (answer) => answer.resolve({ status: "Queued" });
  const deferred = () => {
    const answer = {};
    const send = vi.fn(
      () =>
        new Promise((resolve, reject) => {
          Object.assign(answer, { resolve, reject });
        }),
    );
    return { answer, send };
  };
  const watch = () =>
    renderHook(() => ({
      entry: useRerunSubmission("experiment-a"),
      starts: useRerunStarts("experiment-a"),
    }));
  // Lets every step of the answer run before the case checks what did not happen.
  const settle = (finish) =>
    act(async () => {
      finish();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  const sendBeforeLogout = async () => {
    const first = deferred();
    const onStarted = vi.fn();
    act(() =>
      sendRerunSubmission("experiment-a", sent, {
        send: first.send,
        onStarted,
      }),
    );
    await waitFor(() => expect(first.send).toHaveBeenCalledOnce());
    act(() => resetAllStores());
    return { ...first, onStarted };
  };

  it.each([
    ["fails unconfirmed", unconfirmed],
    ["is confirmed", confirmed],
  ])(
    "leaves the reset store empty when it %s afterwards",
    async (_, finish) => {
      const { result } = watch();
      const first = await sendBeforeLogout();
      await settle(() => finish(first.answer));
      expect(result.current).toEqual({ entry: null, starts: 0 });
      expect(first.onStarted).not.toHaveBeenCalled();
    },
  );

  it.each([
    ["fails unconfirmed", unconfirmed],
    ["is confirmed", confirmed],
  ])(
    "keeps the next session's own submission when the earlier one %s",
    async (_, finish) => {
      const { result } = watch();
      const first = await sendBeforeLogout();
      const next = deferred();
      const nextBody = { append_only: true, prompt_config: [{ name: "Next" }] };
      const nextStarted = vi.fn();
      act(() =>
        sendRerunSubmission(
          "experiment-a",
          { body: nextBody, label: "#2" },
          { send: next.send, onStarted: nextStarted },
        ),
      );
      await waitFor(() => expect(next.send).toHaveBeenCalledOnce());
      expect(next.send.mock.calls[0][0]).toBe(nextBody);
      await settle(() => finish(first.answer));
      expect(result.current.entry).toMatchObject({
        status: "sending",
        body: nextBody,
        label: "#2",
      });
      expect(result.current.starts).toBe(0);
      expect(first.onStarted).not.toHaveBeenCalled();
      // The next session's own answer still lands.
      await settle(() => confirmed(next.answer));
      expect(nextStarted).toHaveBeenCalledWith({ status: "Queued" });
      expect(result.current).toEqual({ entry: null, starts: 1 });
    },
  );
});

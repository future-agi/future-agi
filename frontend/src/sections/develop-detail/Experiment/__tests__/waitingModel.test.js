import { describe, expect, it } from "vitest";
import {
  isExternalWaiting,
  waitingOf,
  waitingStripOf,
  waitTitleOf,
} from "../waitingModel";
import { mergeTray, RUN_TRAY_MAX } from "../runTrayStore";

const NOW = Date.parse("2026-10-16T10:00:00Z");
const run = (extra = {}) => ({
  run_id: "r1",
  label: "#6A",
  kind: "code",
  status: "WaitingForResults",
  progress: { done: 0, total: 20 },
  ...extra,
});

describe("waiting runs", () => {
  it("knows which runs wait on code", () => {
    expect(isExternalWaiting(run())).toBe(true);
    expect(
      isExternalWaiting(
        run({ status: "Running", progress: { done: 5, total: 20 } }),
      ),
    ).toBe(true);
    expect(
      isExternalWaiting(
        run({ status: "Completed", progress: { done: 20, total: 20 } }),
      ),
    ).toBe(false);
    expect(
      isExternalWaiting({
        ...run(),
        kind: "prompt",
        target_type: "prompt",
        status: "Running",
      }),
    ).toBe(false);
  });

  it("writes the live strip: waiting, connected, stalled, done", () => {
    expect(waitingStripOf(run(), null, NOW).text).toBe(
      "Waiting for your terminal · 0 of 20 received",
    );
    const beat = new Date(NOW - 4000).toISOString();
    const live = waitingStripOf(
      run(),
      {
        state: "running",
        received: 37,
        expected: 90,
        host: "alice@mbp-14",
        last_heartbeat_at: beat,
      },
      NOW,
    );
    expect(live.tone).toBe("live");
    expect(live.text).toBe(
      "Connected from alice@mbp-14 · 37 of 90 received · last upload just now",
    );
    const old = new Date(NOW - 180000).toISOString();
    expect(
      waitingStripOf(
        run(),
        {
          state: "running",
          received: 37,
          expected: 90,
          last_heartbeat_at: old,
        },
        NOW,
      ).text,
    ).toBe("No upload for 3 min.");
    expect(
      waitingStripOf(
        run(),
        { state: "completed", received: 90, expected: 90 },
        NOW,
      ).text,
    ).toBe("All 90 received · scoring");
  });
});

describe("run tray", () => {
  it("dedupes, keeps first-seen time and caps", () => {
    let list = mergeTray([], [{ runId: "a" }, { runId: "b" }], 1);
    list = mergeTray(list, [{ runId: "a", label: "#1A" }], 5);
    expect(list.find((e) => e.runId === "a")).toMatchObject({
      label: "#1A",
      addedAt: 1,
    });
    const many = Array.from({ length: 12 }, (_, i) => ({ runId: `r${i}` }));
    expect(mergeTray([], many, 1)).toHaveLength(RUN_TRAY_MAX);
  });
});

describe("waitTitleOf (L3 finalize): the sandbox line follows the run", () => {
  const sandboxRun = {
    run_id: "r1",
    label: "#4A",
    config: {
      kind: "code",
      execution: { mode: "sandbox" },
    },
  };
  it("starting until a row is in, running while rows arrive, ran once every try is in", () => {
    const wait = waitingOf(sandboxRun);
    expect(wait.mode).toBe("sandbox");
    expect(waitTitleOf(wait, null)).toBe(wait.title);
    expect(waitTitleOf(wait, null)).toMatch(/^Starting sandbox · /);
    expect(waitTitleOf(wait, { uploaded: 3 })).toBe(
      `Running in the sandbox · ${wait.where}`,
    );
    expect(waitTitleOf(wait, { uploaded: 30, finished: true })).toBe(
      `Ran in the sandbox · ${wait.where}`,
    );
  });
  it("other modes keep their title", () => {
    const local = waitingOf({
      ...sandboxRun,
      config: { kind: "code", execution: { mode: "local" } },
    });
    expect(local.mode).toBe("local");
    expect(waitTitleOf(local, { finished: true })).toBe(local.title);
    expect(waitTitleOf(null, null)).toBeNull();
  });
});

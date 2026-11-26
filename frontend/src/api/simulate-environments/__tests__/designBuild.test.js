import { afterEach, describe, expect, it, vi } from "vitest";
import { STAGE_MS, VALIDATE_MS, designBuildOf } from "../designBuild";

const draftWith = (files) => ({
  files,
  build: { startedAt: Date.now() },
});

describe("designBuildOf", () => {
  afterEach(() => vi.useRealTimers());

  it("builds stage by stage, validates, then waits for Save to make v1", () => {
    vi.useFakeTimers();
    const draft = draftWith([{ id: "f1", name: "orders.csv" }]);
    expect(designBuildOf({ files: [] })).toBeNull();

    const first = designBuildOf(draft);
    expect(first.status).toBe("building");
    expect(first.stages.map((s) => s.status)).toEqual([
      "running",
      "queued",
      "queued",
      "queued",
    ]);

    vi.advanceTimersByTime(4 * STAGE_MS);
    const checking = designBuildOf(draft);
    expect(checking.status).toBe("validating");
    expect(checking.stages.every((s) => s.status === "done")).toBe(true);
    expect(checking.validation).toBeNull();

    vi.advanceTimersByTime(VALIDATE_MS);
    const ready = designBuildOf(draft);
    expect(ready.status).toBe("ready");
    expect(ready.release).toBeUndefined();
    expect(ready.validation.checks.every((c) => c.ok)).toBe(true);
    const release = { version: 1, name: "", savedAt: 1 };
    expect(designBuildOf({ ...draft, release })).toMatchObject({
      status: "saved",
      release,
    });
  });

  it("needs repair when a records file is broken, naming it and the scenarios reading it", () => {
    vi.useFakeTimers();
    const draft = {
      ...draftWith([
        { id: "f1", name: "policy.pdf" },
        { id: "f2", name: "orders-broken.csv" },
      ]),
      description: "A customer wants a refund. The agent looks up the order.",
    };
    vi.advanceTimersByTime(4 * STAGE_MS + VALIDATE_MS);
    const build = designBuildOf(draft);
    expect(build.status).toBe("needs_repair");
    expect(build.release).toBeUndefined();
    const failed = build.validation.checks.filter((c) => !c.ok);
    expect(failed).toHaveLength(1);
    expect(failed[0].file).toEqual({ id: "f2", name: "orders-broken.csv" });
    expect(failed[0].affects.length).toBeGreaterThan(0);
    expect(failed[0].affects.every((text) => /\border/i.test(text))).toBe(true);
  });

  it("fails at the tool handlers for a failing file, and stops where it is when cancelled", () => {
    vi.useFakeTimers();
    const failing = draftWith([{ id: "f1", name: "tools-crash.json" }]);
    expect(designBuildOf(failing).status).toBe("building");
    vi.advanceTimersByTime(STAGE_MS);
    const failed = designBuildOf(failing);
    expect(failed.status).toBe("failed");
    expect(failed.stages.map((s) => s.status)).toEqual([
      "done",
      "failed",
      "queued",
      "queued",
    ]);
    expect(failed.failure.file).toEqual({ id: "f1", name: "tools-crash.json" });

    const draft = draftWith([{ id: "f1", name: "orders.csv" }]);
    vi.advanceTimersByTime(2 * STAGE_MS);
    const cancelled = {
      ...draft,
      build: { ...draft.build, cancelledAt: Date.now() },
    };
    vi.advanceTimersByTime(10 * STAGE_MS);
    const stopped = designBuildOf(cancelled);
    expect(stopped.status).toBe("cancelled");
    expect(stopped.stages.map((s) => s.status)).toEqual([
      "done",
      "done",
      "cancelled",
      "queued",
    ]);
  });
});

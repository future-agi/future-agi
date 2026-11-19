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

  it("needs repair when a records file is broken, naming it", () => {
    vi.useFakeTimers();
    const draft = draftWith([
      { id: "f1", name: "policy.pdf" },
      { id: "f2", name: "orders-broken.csv" },
    ]);
    vi.advanceTimersByTime(4 * STAGE_MS + VALIDATE_MS);
    const build = designBuildOf(draft);
    expect(build.status).toBe("needs_repair");
    expect(build.release).toBeUndefined();
    const failed = build.validation.checks.filter((c) => !c.ok);
    expect(failed).toHaveLength(1);
    expect(failed[0].file).toEqual({ id: "f2", name: "orders-broken.csv" });
  });
});

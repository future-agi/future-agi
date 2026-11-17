// L7c g7 (ver:not-compared): a refused delta never offers to re-score a run
// on an experiment's own edit ("v2.1"): only template versions are
// re-scoring targets.
import { describe, expect, it } from "vitest";
import { rescoreTargetOf } from "../evalVersionText";

const entry = (run, baseline) => ({
  not_comparable_reason: "eval_versions_differ",
  versions_compared: { run, baseline },
});

describe("rescoreTargetOf", () => {
  it("re-scores the older template version on the newer one", () => {
    expect(
      rescoreTargetOf(entry("v3", "v2"), {
        runId: "r",
        baseId: "b",
        latest: "v3",
      }),
    ).toEqual({ runId: "b", version: "v3" });
  });

  it("offers nothing when one side is an experiment's edit", () => {
    expect(
      rescoreTargetOf(entry("v2.1", "v2"), {
        runId: "r",
        baseId: "b",
        latest: "v3",
      }),
    ).toBeNull();
    expect(
      rescoreTargetOf(entry("v1", "v2.1"), {
        runId: "r",
        baseId: "b",
        latest: "v3",
      }),
    ).toBeNull();
  });
});

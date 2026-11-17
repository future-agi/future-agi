/** options-truth L2 G9: `Changed configs only` from each run vs its parent. */
import { describe, expect, it } from "vitest";
import { changedConfigItem } from "../rerunPlan";

const diff = (a, b) =>
  a.model === b.model ? [] : [{ text: `model ${a.model} → ${b.model}` }];
const run = (label, model, parent = null) => ({
  run_id: label,
  label,
  config: { model },
  parent_run_id: parent,
});

describe("changedConfigItem", () => {
  it("names the runs whose config differs from the run they came from", () => {
    const all = [
      run("#2B", "a"),
      run("#2F", "b", "#2B"),
      run("#2G", "b", "#2F"),
    ];
    const item = changedConfigItem(all, all, diff);
    expect(item.enabled).toBe(true);
    expect(item.runs.map((r) => r.label)).toEqual(["#2F"]);
    expect(item.primary).toBe("Rerun 1 run whose config changed (#2F)");
    expect(item.secondary).toBe("#2F vs #2B: model a → b");
  });
  it("counts every changed run and finds parents outside the experiment", () => {
    const outside = [run("#1A", "a")];
    const mine = [run("#2A", "b", "#1A"), run("#2B", "c", "#1A")];
    const item = changedConfigItem(mine, outside, diff);
    expect(item.primary).toBe("Rerun 2 runs whose config changed (#2A, #2B)");
  });
  it("is disabled with the true reason when nothing changed", () => {
    const same = [run("#2B", "a"), run("#2F", "a", "#2B")];
    expect(changedConfigItem(same, same, diff)).toMatchObject({
      enabled: false,
      secondary: "No run's config changed since its last run",
    });
    expect(changedConfigItem([run("#2A", "a")], [], diff).secondary).toBe(
      "No run here came from another run, so no config changed",
    );
  });
});

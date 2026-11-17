// HO results R8: Save as leaderboard… from By dataset, with the runs rule
// `config_key = "<key>"` read by the one runs scope.
import { describe, expect, it } from "vitest";
import { leaderboardDraftOf } from "../byDatasetModel";
import { configKeyOf } from "src/api/develop/r2";
import { configKeyOf as movedKeyOf } from "src/sections/common/filters/configKey";
import { filterRuns } from "src/sections/common/filters/runsScope";

const run = (id, ds, prompt, model) => ({
  run_id: id,
  dataset_id: ds,
  target_type: "prompt",
  config: {
    prompt: { id: prompt, name: "Support Reply" },
    model: { id: model },
  },
});
const a = run("r1", "d1", "p1", "gpt-4o-mini");
const b = run("r2", "d2", "p1", "gpt-4o-mini");
const c = run("r3", "d2", "p1", "claude-sonnet");

describe("Save as leaderboard from By dataset", () => {
  it("keeps one setup key (r2 re-exports the moved helper)", () => {
    expect(configKeyOf).toBe(movedKeyOf);
    expect(configKeyOf(a)).toBe(configKeyOf(b));
    expect(configKeyOf(a)).not.toBe(configKeyOf(c));
  });
  it("drafts the datasets it ran on, the config rule and rankable measures", () => {
    const key = configKeyOf(a);
    const model = {
      rows: [
        {
          dataset: { datasetId: "d1" },
          measureKey: "eval:correctness",
          cells: { x: { run: a } },
        },
        {
          dataset: { datasetId: "d2" },
          measureKey: "agent:task_success",
          cells: { x: { run: b } },
        },
        {
          dataset: { datasetId: "d3" },
          measureKey: "eval:tone",
          cells: { y: { run: c } },
        },
      ],
    };
    const d = leaderboardDraftOf(model, {
      configKey: key,
      name: "Support Reply × gpt-4o-mini",
      columnKeys: new Set(["eval:correctness", "eval:tone"]),
    });
    expect(d.name).toBe("Support Reply × gpt-4o-mini across datasets");
    expect(d.dataset_ids).toEqual(["d1", "d2"]);
    expect(d.columns).toEqual([{ key: "eval:correctness" }]);
    expect(d.run_filter).toBe(`config_key = ${JSON.stringify(key)}`);
    expect(leaderboardDraftOf(model, { configKey: null })).toBeNull();
  });
  it("the rule picks exactly the runs of that setup", () => {
    const d = leaderboardDraftOf(
      {
        rows: [
          {
            dataset: { datasetId: "d1" },
            measureKey: "k",
            cells: { x: { run: a } },
          },
        ],
      },
      { configKey: configKeyOf(a) },
    );
    const res = filterRuns(d.run_filter, [a, b, c]);
    expect(res.error).toBeUndefined();
    expect(res.runs.map((r) => r.run_id)).toEqual(["r1", "r2"]);
  });
});

import { describe, it, expect } from "vitest";
import { leaderboardFilterDefinition } from "../leaderboardFilterDefinition";
import { filterRuns } from "../leaderboardModel";

const runs = [
  {
    run_id: "R7A",
    status: "Running",
    target_type: "prompt",
    target: {
      model: "gpt-4o-mini",
      prompt_name: "Support Reply",
      prompt_version_name: "V5",
    },
    dataset_version: { number: 6 },
    splits: [{ name: "hard" }],
    created_by: { name: "Maya Chen" },
  },
  {
    run_id: "R8A",
    status: "Cancelled",
    target_type: "http",
    target: { url: "https://api.acme.ai/v1/support/reply" },
    dataset_version: { number: 6 },
    splits: [],
    created_by: { name: "Maya Chen" },
  },
];
const defs = [
  {
    key: "eval:correctness",
    name: "Correctness",
    kind: "eval",
    output_type: "pass_fail",
  },
  { key: "ops:latency", name: "Latency", kind: "system", output_type: "ms" },
];

describe("leaderboard filter definition", () => {
  it("offers the statuses, targets and splits present, with readable labels", () => {
    const d = leaderboardFilterDefinition(runs, defs);
    const byId = Object.fromEntries(
      d.filter((x) => x.propertyId).map((x) => [x.propertyId, x]),
    );
    expect(byId.status.filterType.options).toEqual([
      { value: "Cancelled", label: "Stopped" },
      { value: "Running", label: "Running" },
    ]);
    expect(byId.target.filterType.options.map((o) => o.label)).toEqual([
      "HTTP",
      "Prompt",
    ]);
    expect(byId.split.filterType.options.map((o) => o.label)).toEqual([
      "All rows",
      "hard",
    ]);
    const evals = d.find((x) => x.propertyName === "Evals");
    expect(evals.dependents.map((x) => x.propertyId)).toEqual([
      "metric:eval:correctness",
    ]);
  });

  it("emits rows filterRuns understands (the ComplexFilter shape)", () => {
    const row = {
      column_id: "status",
      filter_config: {
        filter_type: "text",
        filter_op: "equals",
        filter_value: "Running",
      },
      _meta: { parentProperty: "status" },
      id: "x",
    };
    expect(filterRuns(runs, [row]).map((r) => r.run_id)).toEqual(["R7A"]);
    const split = {
      ...row,
      column_id: "split",
      filter_config: { ...row.filter_config, filter_value: "__all__" },
    };
    expect(filterRuns(runs, [split]).map((r) => r.run_id)).toEqual(["R8A"]);
  });
});

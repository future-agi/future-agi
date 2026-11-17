// W5 C-H1: `Filter to selected` is a leaderboard filter row on run ids.
import { describe, expect, it } from "vitest";
import {
  RUN_FILTER_FIELD,
  runFilterOptions,
  runIdsFilter,
  withRunFilter,
} from "../filterToSelected";

describe("w5-c filter to selected", () => {
  it("builds one option filter on the selected run ids", () => {
    expect(runIdsFilter(["r2", "r1", "r2", null])).toEqual({
      column_id: RUN_FILTER_FIELD,
      filter_config: {
        filter_type: "option",
        filter_op: "equals",
        filter_value: ["r2", "r1"],
      },
    });
  });
  it("replaces an earlier run filter and keeps every other filter", () => {
    const tag = {
      column_id: "tag",
      filter_config: {
        filter_type: "option",
        filter_op: "equals",
        filter_value: ["candidate"],
      },
    };
    const once = withRunFilter([tag], ["a", "b"]);
    expect(once).toEqual([tag, runIdsFilter(["a", "b"])]);
    const twice = withRunFilter(once, ["c"]);
    expect(twice).toEqual([tag, runIdsFilter(["c"])]);
    expect(withRunFilter(twice, [])).toEqual([tag]);
    expect(withRunFilter(undefined, ["x"])).toEqual([runIdsFilter(["x"])]);
  });
  it("labels run options by label and name, in label order", () => {
    expect(
      runFilterOptions([
        { run_id: "r10", label: "#10A", name: "Ten" },
        { run_id: "r2", label: "#2A", name: "Two" },
        { run_id: "r3" },
        null,
      ]),
    ).toEqual([
      { value: "r2", label: "#2A Two" },
      { value: "r10", label: "#10A Ten" },
      { value: "r3", label: "r3" },
    ]);
  });
});

// MUST 8: the `Run on which dataset?` picker's pure parts and
// the doors' URL keys.
import { describe, expect, it } from "vitest";
import { datasetLine, matchDatasets } from "../RunOnRowsButton";
import { doorParams } from "src/sections/develop-detail/ExperimentTab/useOpenSource";

describe("Run on which dataset?", () => {
  const list = [
    { datasetId: "a", name: "Support ops tasks · sandbox", row_count: 30 },
    { datasetId: "b", name: "Support agent - golden set", row_count: 1 },
    { datasetId: "c", name: "RAG citations" },
    { name: "no id" },
  ];
  it("names a dataset with its rows", () => {
    expect(datasetLine(list[0])).toBe("Support ops tasks · sandbox · 30 rows");
    expect(datasetLine(list[1])).toBe("Support agent - golden set · 1 row");
    expect(datasetLine(list[2])).toBe("RAG citations");
  });
  it("keeps server order and filters by name", () => {
    expect(matchDatasets(list, "").map((d) => d.datasetId)).toEqual([
      "a",
      "b",
      "c",
    ]);
    expect(matchDatasets(list, "GOLDEN").map((d) => d.datasetId)).toEqual([
      "b",
    ]);
  });
});

describe("doors into the drawer", () => {
  it("opens a kind's catalog, an entry, or R3's prompt / agent card", () => {
    expect(doorParams({ cardId: "harness" })).toEqual({
      new_experiment: "1",
      src: "harness",
      entry: null,
    });
    expect(doorParams({ cardId: "running" }).src).toBe("running");
    expect(doorParams({ entryId: "langgraph" })).toEqual({
      new_experiment: "1",
      entry: "langgraph",
      src: null,
    });
    expect(doorParams({ cardId: "prompt" })).toEqual({ new_experiment: "1" });
  });
});

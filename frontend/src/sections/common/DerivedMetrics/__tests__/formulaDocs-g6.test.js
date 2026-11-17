// L7c g6 (metric:new, RELABEL): an eval field's help says exactly what it
// reads — rows passing (a row passes on at least half its tries), never
// "pass rate", which the Summary reads per try.
import { describe, expect, it } from "vitest";
import { evalFieldDocs, formulaCatalog } from "../formulaModel";

const DEFS = [
  {
    key: "eval:task_success",
    name: "Task success",
    kind: "eval",
    output_type: "pass_fail",
  },
  {
    key: "eval:tool_call_accuracy",
    name: "Tool-call accuracy",
    kind: "eval",
    output_type: "score",
  },
];

describe("run metric field help (L7c g6)", () => {
  it("names rows passing for a pass/fail eval, the mean for a score", () => {
    const docs = evalFieldDocs(DEFS);
    expect(docs[0]).toEqual({
      name: "task_success",
      what: "share of rows that pass (a row passes when at least half of its tries pass; not the per-try pass rate)",
    });
    expect(docs[1].what).toBe(
      "mean score over rows (each row's mean over its tries)",
    );
  });
  it("the catalogue doc carries the same words (no bare 'pass rate')", () => {
    const f = formulaCatalog(DEFS).fields.find(
      (x) => x.name === "task_success",
    );
    expect(f.doc).toBe(
      "Task success: share of rows that pass (a row passes when at least half of its tries pass; not the per-try pass rate)",
    );
    expect(f.doc).not.toMatch(/\(pass rate\)/);
  });
});

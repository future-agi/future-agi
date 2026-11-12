// L7c g7 (test:editor, mapping:variable-mapping, evalcreate:composite).
import { describe, expect, it } from "vitest";
import {
  lockedOutputVariables,
  virtualOutputOf,
  virtualOutputValueOf,
  withLockedOutput,
} from "./virtualOutputModel";
import { axisOptionsFor } from "./CompositeAxisTabs";

const extras = [
  {
    field: "output",
    headerName: "Output",
    sampleFrom: "col-reply",
    sampleName: "Draft reply (gpt-4o-mini)",
  },
  { field: "prompt_chain", headerName: "Prompt Chain" },
];

describe("virtual Output in an experiment host", () => {
  it("names the host's run-output column and its Test stand-in", () => {
    expect(virtualOutputOf(extras)).toEqual({
      name: "Output",
      sampleFrom: "col-reply",
      sampleName: "Draft reply (gpt-4o-mini)",
    });
    expect(virtualOutputOf([{ field: "prompt_chain" }])).toBeNull();
    expect(virtualOutputOf(undefined)).toBeNull();
  });

  it("locks the reply variables to Output (no dataset-column remap)", () => {
    const vo = virtualOutputOf(extras);
    const locked = lockedOutputVariables(["output", "context", "input"], vo);
    expect(locked).toEqual(["output"]);
    expect(lockedOutputVariables(["output"], null)).toEqual([]);
    const m = { output: "expected_reply", context: "policy_kb" };
    expect(withLockedOutput(m, locked, vo)).toEqual({
      output: "Output",
      context: "policy_kb",
    });
    const ok = { output: "Output" };
    expect(withLockedOutput(ok, locked, vo)).toBe(ok);
  });

  it("Test sends the sample column's value on the row on screen", () => {
    const vo = virtualOutputOf(extras);
    const row = { "col-reply": { cell_value: "Refunded A-4411." } };
    const unwrap = (c) => c?.cell_value ?? c;
    expect(virtualOutputValueOf(vo, row, unwrap)).toBe("Refunded A-4411.");
    expect(virtualOutputValueOf(vo, {}, unwrap)).toBeUndefined();
    expect(
      virtualOutputValueOf({ ...vo, sampleFrom: null }, row, unwrap),
    ).toBeUndefined();
  });
});

describe("composite child axis", () => {
  it("offers Pass / Fail and Score only (an existing axis still shows)", () => {
    expect(axisOptionsFor("pass_fail").map((t) => t.value)).toEqual([
      "pass_fail",
      "percentage",
    ]);
    expect(axisOptionsFor("choices").map((t) => t.value)).toEqual([
      "pass_fail",
      "percentage",
      "choices",
    ]);
  });
});

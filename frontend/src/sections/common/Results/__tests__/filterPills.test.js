import { describe, expect, it } from "vitest";
import {
  PILL_TYPES,
  movementPill,
  parsePills,
  pillLabel,
  samePill,
  serializePills,
  setMovementPill,
  togglePill,
} from "../filterPills";

const runs = [
  { run_id: "RUN_3A", label: "#3A" },
  { run_id: "RUN_4A", label: "#4A" },
];
const metrics = [
  { key: "eval:task_completion", name: "Task completion" },
  { key: "eval:groundedness", name: "Groundedness" },
];

describe("filterPills", () => {
  it("covers the §3.10.5 pill types (+ R2 rows)", () => {
    expect(Object.keys(PILL_TYPES).sort()).toEqual(
      [
        "rows",
        "cost",
        "error",
        "fails",
        "flaky",
        "human",
        "latency",
        "meta",
        "movement",
        "pref",
        "pref_disagree",
        "score",
        "split",
        "tool_error",
        // integration S1: the expression pill.
        "expression",
      ].sort(),
    );
  });

  it("round-trips through the URL", () => {
    const pills = [
      movementPill("eval:task_completion", "regressed", "RUN_4A"),
      { type: "split", values: ["hard"] },
    ];
    const str = serializePills(pills);
    expect(parsePills(str)).toEqual(pills);
    expect(parsePills(encodeURIComponent(str))).toEqual(pills);
    expect(parsePills(pills)).toEqual(pills);
    expect(serializePills([])).toBe("");
  });

  it("drops junk without throwing", () => {
    expect(parsePills("")).toEqual([]);
    expect(parsePills("not json")).toEqual([]);
    expect(parsePills("%E0%A4%A")).toEqual([]);
    expect(parsePills('{"type":"split"}')).toEqual([]);
    expect(
      parsePills('[{"type":"bogus"},{"type":"error","run":"RUN_4A","x":1}]'),
    ).toEqual([{ type: "error", run: "RUN_4A" }]);
  });

  it("labels pills in plain text", () => {
    const ctx = { runs, metrics, baselineId: "RUN_3A" };
    expect(
      pillLabel(
        movementPill("eval:task_completion", "regressed", "RUN_4A"),
        ctx,
      ),
    ).toBe("Regressed on Task completion · #4A vs #3A");
    expect(
      pillLabel(movementPill("eval:task_completion", "improved"), ctx),
    ).toBe("Improved on Task completion · any candidate vs #3A");
    expect(pillLabel({ type: "split", values: ["hard"] }, ctx)).toBe(
      "Split: hard",
    );
    expect(pillLabel({ type: "split", values: ["__none__"] }, ctx)).toBe(
      "Split: no split",
    );
    expect(
      pillLabel(
        {
          type: "score",
          metric: "eval:groundedness",
          op: "lt",
          value: 0.7,
          run: "RUN_4A",
        },
        ctx,
      ),
    ).toBe("Groundedness < 0.7 · #4A");
    expect(pillLabel({ type: "error", run: "any_run" }, ctx)).toBe(
      "Errored · any run",
    );
    expect(
      pillLabel(
        { type: "meta", key: "intent", op: "eq", value: "refund_request" },
        ctx,
      ),
    ).toBe("intent = refund_request");
    expect(
      pillLabel({ type: "latency", op: "gt", value: 3000, run: "RUN_4A" }, ctx),
    ).toBe("Latency > 3 s · #4A");
    expect(
      pillLabel({ type: "pref", value: "tie", source: "human" }, ctx),
    ).toBe("Tie · human");
    expect(
      pillLabel({ type: "pref", value: "RUN_4A", source: "judge" }, ctx),
    ).toBe("#4A preferred · LLM judge");
    expect(pillLabel({ type: "nope" }, ctx)).toBe("");
  });

  it("toggles and replaces movement pills", () => {
    const p = movementPill("eval:task_completion", "regressed", "RUN_4A");
    const on = togglePill([], p);
    expect(on).toHaveLength(1);
    expect(samePill(on[0], { ...p })).toBe(true);
    expect(togglePill(on, p)).toEqual([]);
    const swapped = setMovementPill(
      on,
      movementPill("eval:task_completion", "improved", "RUN_4A"),
    );
    expect(swapped).toEqual([
      movementPill("eval:task_completion", "improved", "RUN_4A"),
    ]);
  });
});

// ------------------------------------------------------------- Round 2
import { rowIdsFromPills, rowsPill } from "../filterPills";

describe("rows pill (R2)", () => {
  it("builds, serializes, parses and names", () => {
    const pill = rowsPill(["r1", "r2", "r2"], {
      label: "Failure mode: wrong order id",
    });
    expect(pill).toEqual({
      type: "rows",
      ids: ["r1", "r2"],
      label: "Failure mode: wrong order id",
    });
    const back = parsePills(serializePills([pill]));
    expect(rowIdsFromPills(back)).toEqual(["r1", "r2"]);
    expect(pillLabel(back[0])).toBe("Failure mode: wrong order id (2)");
    expect(
      pillLabel(rowsPill(Array.from({ length: 6 }, (_, i) => `r${i}`))),
    ).toBe("Rows (6)");
    expect(rowIdsFromPills([])).toBeNull();
  });
});

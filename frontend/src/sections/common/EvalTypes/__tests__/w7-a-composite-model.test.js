import { describe, expect, it } from "vitest";
import {
  AGGREGATION_COPY,
  VOTE_PASS_LINE,
  addCheck,
  addSeat,
  compactLine,
  compositeExtrasBody,
  removeRow,
  rowKeyOf,
  rowsFromDetail,
  splitLine,
  suiteCheckKeysOf,
  usesWeights,
  validateComposite,
  withSeats,
} from "../compositeModel";

// W7 A1: the composite editor's pure model.
const COR = { child_id: "t-cor", child_name: "Correctness", weight: 1 };
const TONE = { child_id: "t-tone", child_name: "Tone", weight: 1 };

describe("compositeModel", () => {
  it("the new aggregations' copy, verbatim", () => {
    expect(AGGREGATION_COPY.majority).toBe(
      "More than half the weight must pass. A tie fails.",
    );
    expect(AGGREGATION_COPY.all_pass).toBe("Every child must pass.");
    expect(VOTE_PASS_LINE.majority).toBe(
      "Passes when more than half the weight passes",
    );
    expect(VOTE_PASS_LINE.all_pass).toBe("Passes only when every child passes");
    expect(usesWeights("majority")).toBe(true);
    expect(usesWeights("avg")).toBe(false);
  });

  it("validation: majority needs 2 children, weights above 0, every seat a judge", () => {
    expect(
      validateComposite({ aggregation: "majority", children: [COR] }),
    ).toEqual(["Majority needs at least 2 children"]);
    expect(
      validateComposite({
        aggregation: "weighted_avg",
        children: [COR, TONE],
        weights: { "t-tone": 0 },
      }),
    ).toEqual(["Give Tone a weight above 0"]);
    const seated = addSeat([TONE], TONE);
    expect(
      validateComposite({ aggregation: "majority", children: seated }),
    ).toEqual(["Pick a judge for seat 2 of Tone"]);
    const twice = withSeats([
      { ...TONE, judge_model: "gpt-4.1" },
      { ...TONE, judge_model: "gpt-4.1" },
    ]);
    expect(
      validateComposite({ aggregation: "majority", children: twice }),
    ).toEqual(["Tone has two seats with gpt-4.1"]);
    expect(validateComposite({ aggregation: "min", children: [COR] })).toEqual(
      [],
    );
  });

  it("seats number per template; removing one renumbers the rest", () => {
    let rows = addSeat([COR, TONE], TONE);
    rows = addSeat(rows, TONE);
    expect(rows.map((r) => [r.child_id, r.seat, r.kind])).toEqual([
      ["t-cor", 1, "eval"],
      ["t-tone", 1, "eval"],
      ["t-tone", 2, "seat"],
      ["t-tone", 3, "seat"],
    ]);
    rows = removeRow(rows, "t-tone#2");
    expect(rows.map(rowKeyOf)).toEqual(["t-cor#1", "t-tone#1", "t-tone#2"]);
  });

  it("the body carries seats, levels and checks; a plain composite adds nothing", () => {
    expect(compositeExtrasBody([COR, TONE], { "t-cor": 2 })).toEqual({});
    let rows = addSeat([COR, TONE], TONE);
    rows = rows.map((r) =>
      r.seat === 2
        ? {
            ...r,
            judge_model: "gpt-4.1",
            level: "step",
            step_kind: "tool",
            reduce: "mean",
          }
        : r,
    );
    rows = addCheck(rows, { key: "order_found", name: "Order found" });
    const body = compositeExtrasBody(rows, { "t-cor": 2 });
    expect(body.child_template_ids).toEqual(["t-cor", "t-tone", "t-tone"]);
    expect(body.child_kinds).toEqual({
      "t-cor#1": "eval",
      "t-tone#1": "eval",
      "t-tone#2": "seat",
      "check:order_found": "check",
    });
    expect(body.child_judge_models).toEqual({ "t-tone#2": "gpt-4.1" });
    expect(body.child_levels["t-tone#2"]).toEqual({
      level: "step",
      step_kind: "tool",
      reduce: "mean",
    });
    expect(body.child_weights["t-cor#1"]).toBe(2);
    expect(body.child_weights["t-cor"]).toBe(2);
  });

  it("detail rows read back the stored seat, judge and level", () => {
    const rows = rowsFromDetail({
      children: [{ ...TONE }, { ...TONE }],
      child_judge_models: { "t-tone#1": "gpt-4o-mini", "t-tone#2": "gpt-4.1" },
      child_levels: {
        "t-tone#2": { level: "step", step_kind: "llm", reduce: "min" },
      },
    });
    expect(rows[0]).toMatchObject({
      seat: 1,
      kind: "eval",
      judge_model: "gpt-4o-mini",
    });
    expect(rows[1]).toMatchObject({
      seat: 2,
      kind: "seat",
      judge_model: "gpt-4.1",
      level: "step",
      reduce: "min",
    });
  });

  it("the compact line and the split line", () => {
    const typed = {
      kind: "composite",
      aggregation: "majority",
      disagreement: true,
      agreement: { split: "2–1" },
      children: [
        {
          name: "Correctness",
          weight: 2,
          passed: true,
          value: 0.82,
          seat: 1,
          kind: "eval",
        },
        {
          name: "Tone",
          weight: 1,
          passed: false,
          value: 0.31,
          seat: 1,
          kind: "eval",
        },
      ],
    };
    expect(compactLine(typed, true)).toBe(
      "Correctness ×2 Pass 0.82 · Tone ×1 Fail 0.31 · majority → Pass",
    );
    expect(splitLine(typed)).toBe("Judges disagree (2–1)");
    expect(splitLine({ ...typed, disagreement: false })).toBeNull();
  });

  it("one eval read on different steps splits as children, not as judges", () => {
    const TONE = "0e000000-0000-4000-8000-000000000003";
    const child = (seat, level, stepKind, passed) => ({
      name: "Tone",
      template_id: TONE,
      seat,
      kind: seat === 1 ? "eval" : "seat",
      level,
      ...(stepKind ? { step_kind: stepKind } : {}),
      passed,
      value: passed ? 0.88 : 0.49,
      weight: 1,
    });
    const steps = {
      kind: "composite",
      aggregation: "all_pass",
      disagreement: true,
      agreement: { split: "1–1" },
      children: [
        child(1, "step", "tool", false),
        child(2, "step", "llm", true),
      ],
    };
    expect(splitLine(steps)).toBe("Children split (1–1)");
    const jury = {
      ...steps,
      children: [child(1, "row", null, false), child(2, "row", null, true)],
    };
    expect(splitLine(jury)).toBe("Judges disagree (1–1)");
  });

  it("Add check lists each task-suite check key once, with its task count", () => {
    const columns = [
      { id: "c-q", name: "task" },
      { id: "c-k", name: "checks" },
    ];
    const table = [
      {
        "c-k": {
          cell_value: JSON.stringify([
            { key: "order_found", name: "Order found" },
            { key: "refund_issued", name: "Refund issued" },
          ]),
        },
      },
      { "c-k": { cell_value: [{ key: "order_found", name: "Order found" }] } },
      { "c-k": { cell_value: "not json" } },
      { "c-q": { cell_value: "no checks" } },
    ];
    expect(suiteCheckKeysOf(table, columns, "Support ops")).toEqual([
      {
        key: "order_found",
        name: "Order found",
        suite: "Support ops",
        rows: 2,
      },
      {
        key: "refund_issued",
        name: "Refund issued",
        suite: "Support ops",
        rows: 1,
      },
    ]);
    expect(suiteCheckKeysOf(table, [{ id: "c-q", name: "task" }])).toEqual([]);
  });

  it("the detail's check children come back as check rows after the templates", () => {
    const rows = rowsFromDetail({
      children: [{ child_id: "t-tone", child_name: "Tone", weight: 1 }],
      check_children: [
        {
          child_id: "check:order_found",
          child_name: "order_found",
          kind: "check",
          check_key: "order_found",
          weight: 2,
        },
      ],
      child_judge_models: { "t-tone#1": "gpt-4o-mini" },
    });
    expect(
      rows.map((r) => [rowKeyOf(r), r.kind, r.judge_model ?? null]),
    ).toEqual([
      ["t-tone#1", "eval", "gpt-4o-mini"],
      ["check:order_found", "check", null],
    ]);
  });
});

import { describe, expect, it } from "vitest";
import { doorNote, productionOfSelection, retestEvals } from "../retestEvals";
import { unsavedBannerSentence } from "../useUnsavedSelection";
import { conversationSummary, shortValue } from "../versionText";
import { pendingSampleRows, sampleRowLabelOf } from "../selectedSample";

const P = {
  outcome: "fail",
  criterion: "Resolution",
  reason: "r",
  failing_turn: 2,
  reply: "x",
  template_id: "t-res",
};
const ops = [1, 2].map((i) => ({
  op: "add_row",
  temp_id: `imp-${i}`,
  cells: { c1: `msg ${i}`, cExp: null },
  production: P,
}));
const columns = [
  { id: "c1", name: "customer_message", role: "input" },
  { id: "cExp", name: "expected_reply", role: "expected" },
];
const correctness = {
  name: "Correctness",
  templateId: "t-corr",
  isDefault: true,
  config: { mapping: { output: "output", expected: "cExp" } },
};
const tone = {
  name: "Tone",
  templateId: "t-tone",
  isDefault: true,
  config: { mapping: { output: "output" } },
};
const selected = ops.map((o) => ({ id: o.temp_id, cells: (c) => o.cells[c] }));

describe("re-test defaults (IT-R2 G5 P1)", () => {
  it("reads the production criterion of every selected row", () => {
    expect(
      productionOfSelection({ rowIds: ["imp-1", "imp-2"], ops }),
    ).toMatchObject({
      count: 2,
      criterion: "Resolution",
      template_id: "t-res",
    });
    expect(
      productionOfSelection({ rowIds: ["imp-1", "row-9"], ops, rows: [] }),
    ).toBeNull();
    expect(
      productionOfSelection({
        rowIds: ["r1"],
        rows: [{ row_id: "r1", row_meta: { production: P } }],
      })?.criterion,
    ).toBe("Resolution");
  });

  it("puts Resolution first and drops a default that would skip every row", () => {
    const out = retestEvals([correctness, tone], {
      production: P,
      columns,
      selected,
    });
    expect(out.map((e) => e.name)).toEqual(["Resolution", "Tone"]);
    expect(out[0]).toMatchObject({ templateId: "t-res", fromProduction: true });
    // user-added (not default) evals stay even when they would skip
    const own = { ...correctness, isDefault: false };
    expect(
      retestEvals([own], { production: P, columns, selected }).map(
        (e) => e.name,
      ),
    ).toEqual(["Resolution", "Correctness"]);
  });

  it("names the version note and the banner like the footer", () => {
    expect(doorNote(ops)).toBe("Added 2 failed production sessions");
    expect(doorNote([{ op: "add_row", temp_id: "new-1" }])).toBe("Added 1 row");
    expect(
      unsavedBannerSentence(
        { total: 6, unsaved: 6 },
        { versionNumber: 6, next: 7, runs: 1 },
      ),
    ).toBe(
      "6 new rows (not saved yet) × 1 run = 6 tries. Save them as v7 to run them.",
    );
  });

  it("summarises a conversation in the save dialog, never its JSON", () => {
    const conv = {
      turns: Array.from({ length: 8 }, (_, i) => ({
        n: i + 1,
        role: i % 2 ? "agent" : "customer",
        content: "…",
      })),
      resolution: "Fail",
      failing_turn: 2,
    };
    expect(conversationSummary(conv)).toBe(
      "Conversation · 8 turns · failed at turn 2 (Resolution)",
    );
    expect(shortValue(JSON.stringify(conv))).toBe(
      "Conversation · 8 turns · failed at turn 2 (Resolution)",
    );
    expect(shortValue("plain")).toBe("plain");
  });

  it("offers pending door rows as sample rows", () => {
    const rows = pendingSampleRows(ops, ["imp-2"]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      row_id: "imp-2",
      pending_no: 2,
      c1: { cell_value: "msg 2" },
    });
    expect(sampleRowLabelOf(rows[0])).toBe("New row 2");
    expect(sampleRowLabelOf({ order: 250 })).toBe("Row 251");
  });
});

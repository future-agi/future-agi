import { describe, expect, it } from "vitest";
import {
  emptyInputText,
  emptyInputWarnings,
  rowsSendLine,
} from "../reviewModel";

const cols = [
  {
    id: "c1",
    name: "customer_message",
    data_type: "text",
    stats: { empty_count: 0, total: 254 },
  },
  {
    id: "c2",
    name: "expected_reply",
    data_type: "text",
    stats: { empty_count: 6, total: 254 },
  },
  { id: "c3", name: "screenshot", data_type: "image" },
];
const evals = [
  {
    name: "Correctness",
    config: { mapping: { output: "output", expected: "c2" } },
  },
];

describe("reviewModel (R4 P4, P6)", () => {
  it("a selection: counts its own empty rows", () => {
    const rows = Array.from({ length: 6 }, (_, i) => ({
      row_id: `r${i}`,
      c2: { cell_value: "" },
    }));
    const [w] = emptyInputWarnings(evals, rows, cols);
    expect(w).toMatchObject({
      evalName: "Correctness",
      column: "expected_reply",
      missing: 6,
      total: 6,
    });
    expect(emptyInputText(w)).toBe(
      "Correctness needs expected_reply · 6 of 6 rows have none · those rows will show Skipped, not Pass",
    );
  });
  it("all rows: the column stats; numbers follow the data", () => {
    const [w] = emptyInputWarnings(evals, [], cols, { useStats: true });
    expect(w).toMatchObject({ missing: 6, total: 254 });
    const none = emptyInputWarnings(
      evals,
      [],
      [{ ...cols[1], stats: { empty_count: 0, total: 9 } }],
      { useStats: true },
    );
    expect(none).toEqual([]);
  });
  it("what each row sends, with modality", () => {
    expect(
      rowsSendLine(
        [{ variableMapping: { user_message: "c1", img: "c3" } }],
        cols,
      ),
    ).toBe(
      // IT-R2 G1 P10: each prompt variable names the column that feeds it.
      "Each row sends user_message ← customer_message · img ← screenshot (image)",
    );
    expect(
      rowsSendLine([{ r4: { entryId: "langgraph" } }], cols, {
        inputColumn: "customer_message",
      }),
    ).toBe("Each row sends customer_message (text)");
    expect(rowsSendLine([], cols)).toBe(null);
  });
});

describe("reviewModel · name mappings", () => {
  it("a dataset eval mapped by column name is checked too", () => {
    const rows = [
      { row_id: "a", c2: { cell_value: "" } },
      { row_id: "b", c2: { cell_value: "x" } },
    ];
    const [w] = emptyInputWarnings(
      [{ name: "Correctness", mapping: { expected: "expected_reply" } }],
      rows,
      [{ id: "c2", name: "expected_reply" }],
    );
    expect(w).toMatchObject({ missing: 1, total: 2 });
  });
});

describe("IT-R2 G1 P7 · runs that differ are flagged", () => {
  it("names each harness's model and the at-once gap", async () => {
    const { runsCompareTable } = await import("../reviewModel");
    const cards = [
      {
        r4: {
          kind: "harness",
          entryName: "Claude Code",
          limits: { concurrency: 2 },
          slot: {
            harness: {
              model: "claude-sonnet-4-5",
              budget: { max_cost_usd: 1 },
            },
          },
        },
      },
      {
        r4: {
          kind: "harness",
          entryName: "Codex CLI",
          limits: { concurrency: 4 },
          slot: {
            harness: { model: "gpt-5-codex", budget: { max_cost_usd: 1 } },
          },
        },
      },
    ];
    const runs = [
      { letter: "A", name: "Claude Code", configIndex: 0 },
      { letter: "B", name: "Codex CLI", configIndex: 1 },
    ];
    const t = runsCompareTable(cards, runs, { ordinal: 4 });
    expect(t.rows[0]).toMatchObject({
      label: "#4A",
      model: "claude-sonnet-4-5",
      atOnce: 2,
      capPerTry: 1,
    });
    expect(t.differ).toBe(
      "Runs differ: Claude Code uses claude-sonnet-4-5, Codex CLI uses gpt-5-codex (each harness runs its own vendor's models) · 2 vs 4 at a time",
    );
    expect(t.limitsDiffer).toBe(true);
  });
});

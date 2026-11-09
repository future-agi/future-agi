import { describe, expect, it } from "vitest";
import {
  INPUT_SYNONYMS,
  RUN_ON_ROWS_MAX_IDS,
  inputColumnFor,
  parseRunOnRows,
  runOnRowsHref,
  runOnRowsTarget,
} from "../runOnRows";

const DS = "d5a1e7c2-0b3f-4a8d-9e61-1c2b3a4d5e01";
const GOLDEN_COLUMNS = [
  { name: "customer_message", data_type: "text" },
  { name: "conversation", data_type: "json" },
  { name: "expected_reply", data_type: "text" },
  { name: "intent", data_type: "text" },
];

describe("runOnRowsHref / parseRunOnRows (D24)", () => {
  it("builds the drawer deep link and reads it back", () => {
    const href = runOnRowsHref({
      datasetId: DS,
      rowIds: ["r1", "r2"],
      source: "prompt",
      promptId: "p1",
      promptVersionId: 8,
    });
    expect(href).toBe(
      `/dashboard/develop/${DS}?tab=experiments&new_experiment=1&rows=r1%2Cr2&source=prompt&prompt_id=p1&prompt_version=8`,
    );
    expect(parseRunOnRows(href.split("?")[1])).toEqual({
      open: true,
      rows: ["r1", "r2"],
      split: null,
      source: "prompt",
      entry: null,
      agent: null,
      mode: null,
      form: null,
      promptId: "p1",
      promptVersion: "8",
      // L7c g8 (entry:run-on-rows:training-split): the link carries the
      // dataset version it was made on; none here.
      datasetVersionId: null,
    });
  });

  it("sends more than 50 ids through the selection prefill", () => {
    const ids = Array.from(
      { length: RUN_ON_ROWS_MAX_IDS + 1 },
      (_, i) => `r${i}`,
    );
    const t = runOnRowsTarget({ datasetId: DS, rowIds: ids });
    expect(t.href).toContain("rows=selection");
    expect(t.prefill.row_ids).toHaveLength(51);
    expect(parseRunOnRows(t.href.split("?")[1]).rows).toBe("selection");
  });

  it("uses a split when no rows are picked", () => {
    expect(runOnRowsHref({ datasetId: DS, split: "smoke" })).toContain(
      "&split=smoke",
    );
    expect(parseRunOnRows("?new_experiment=1&form=connect").form).toBe(
      "connect",
    );
    expect(parseRunOnRows("").open).toBe(false);
  });
});

describe("inputColumnFor (D24)", () => {
  it("maps user_message to customer_message on the golden set (synonym)", () => {
    expect(inputColumnFor("user_message", GOLDEN_COLUMNS)).toEqual({
      column: "customer_message",
      how: "synonym",
    });
  });
  it("prefers the same name, then falls back to the last customer turn", () => {
    expect(inputColumnFor("intent", GOLDEN_COLUMNS)).toEqual({
      column: "intent",
      how: "same name",
    });
    expect(
      inputColumnFor("question", [{ name: "conversation", data_type: "json" }]),
    ).toEqual({
      column: "conversation",
      how: "last customer turn",
      path: "last customer turn",
    });
    expect(inputColumnFor("order_id", GOLDEN_COLUMNS)).toBeNull();
  });
  it("lists the synonyms in preference order", () => {
    expect(INPUT_SYNONYMS).toEqual([
      "customer_message",
      "user_message",
      "message",
      "input",
      "question",
      "instruction",
      "query",
      "prompt",
    ]);
  });
});

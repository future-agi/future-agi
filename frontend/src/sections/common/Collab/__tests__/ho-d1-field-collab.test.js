// HO D1 / D2: a field's Translate to… / Comment on this field and the grid
// cell's Translate cell… deep link.
import { describe, expect, it, vi } from "vitest";
import {
  COMMENT_ANCHOR_PARAM,
  TRANSLATE_FIELD_PARAM,
  canTranslateCell,
  fieldAnchorKey,
  fieldCommentDeepLink,
  fieldTargets,
  noTargetText,
  translateCellDeepLink,
  withoutTranslateParam,
} from "../fieldCollabModel";
import { fieldAnchorOptions } from "../commentModel";
import { buildCellContextMenu } from "src/sections/develop-detail/DataTab/DataGrid/gridContextMenu";

const INFO = {
  languages: [
    { code: "en", name: "English" },
    { code: "es", name: "Spanish" },
    { code: "hi", name: "Hindi" },
  ],
  fields: [
    {
      column_id: "c1",
      name: "customer_message",
      language: "es",
      language_name: "Spanish",
      available: ["hi", "en"],
    },
    {
      column_id: "c2",
      name: "expected_reply",
      language: "en",
      language_name: "English",
      available: [],
    },
    { column_id: "c3", name: "notes", language: null, available: [] },
  ],
};

describe("HO D1 · a field's ⋯ menu", () => {
  it("offers the targets the translate read gives that field, English first", () => {
    expect(fieldTargets(INFO, "customer_message")).toEqual([
      { code: "en", name: "English", fields: ["c1"] },
      { code: "hi", name: "Hindi", fields: ["c1"] },
    ]);
    expect(fieldTargets(INFO, "expected_reply")).toEqual([]);
  });

  it("says why a field has nothing to translate", () => {
    expect(noTargetText(INFO, "expected_reply", "row 4")).toBe(
      "No translation of expected_reply on row 4 from English is available.",
    );
    expect(noTargetText(INFO, "notes")).toBe(
      "notes is empty, so there is nothing to translate.",
    );
    expect(noTargetText(INFO, "ticket_id")).toBe(
      "ticket_id is not a text field that can be translated.",
    );
  });

  it("Comment on this field: Activity › Comments, composer on the field", () => {
    const next = fieldCommentDeepLink(
      new URLSearchParams("tab=data&rtranslate=c1"),
      "r9",
      "c1",
      42,
    );
    expect(Object.fromEntries(next)).toEqual({
      tab: "data",
      row: "r9",
      rowTab: "activity",
      ract: "comments",
      rcs: "open",
      rcompose: "42",
      [COMMENT_ANCHOR_PARAM]: "field:c1",
    });
    // The key is one of the composer's anchor options.
    const opts = fieldAnchorOptions([
      { id: "c1", name: "customer_message", role: "input" },
    ]);
    expect(opts.map((o) => o.key)).toContain(fieldAnchorKey("c1"));
  });
});

describe("HO D2 · Translate cell… deep link", () => {
  it("opens the row on its Row tab with that field named; handled once", () => {
    const next = translateCellDeepLink(
      new URLSearchParams("tab=data&rowTab=activity"),
      "r9",
      "c1",
    );
    expect(Object.fromEntries(next)).toEqual({
      tab: "data",
      rowTab: "row",
      row: "r9",
      [TRANSLATE_FIELD_PARAM]: "c1",
    });
    expect(withoutTranslateParam(next).has(TRANSLATE_FIELD_PARAM)).toBe(false);
  });

  it("is offered on a user text cell with a value only", () => {
    const text = { dataType: "text", col: { is_user_data: true } };
    expect(canTranslateCell(text, "hola")).toBe(true);
    expect(canTranslateCell(text, "  ")).toBe(false);
    expect(canTranslateCell({ ...text, dataType: "integer" }, "3")).toBe(false);
    expect(
      canTranslateCell(
        {
          dataType: "text",
          col: { is_user_data: false },
          originType: "run_prompt",
        },
        "hi",
      ),
    ).toBe(false);
  });

  it("puts Translate cell… in the grid's cell menu and hands the column id", () => {
    const translateCell = vi.fn();
    const params = {
      column: {
        getColId: () => "c1",
        getColDef: () => ({
          dataType: "text",
          col: { id: "c1", is_user_data: true },
        }),
      },
      node: { data: { row_id: "r9", row_meta: {} } },
      value: "¿Dónde está mi pedido?",
    };
    const handlers = {
      filterTo: vi.fn(),
      exclude: vi.fn(),
      showAllIn: vi.fn(),
      openRow: vi.fn(),
      copyValue: vi.fn(),
      copyRowLink: vi.fn(),
      addToQueue: vi.fn(),
      toggleSplit: vi.fn(),
      manageSplits: vi.fn(),
      translateCell,
    };
    const items = buildCellContextMenu(params, { handlers });
    const item = items.find((i) => i?.name === "Translate cell…");
    expect(item).toBeTruthy();
    item.action();
    expect(translateCell).toHaveBeenCalledWith("r9", "c1");
    // Without the handler (another host), the item is absent.
    const { translateCell: _t, ...rest } = handlers;
    expect(
      buildCellContextMenu(params, { handlers: rest }).some(
        (i) => i?.name === "Translate cell…",
      ),
    ).toBe(false);
  });
});

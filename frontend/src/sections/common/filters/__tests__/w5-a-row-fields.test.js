// W5 A6 · registered row fields never shadow a dataset's own names
// (catalogFields.withRowFields): a column, metadata key, eval or field keeps
// its name in the grid and in results alike; a free name is offered.
import { afterEach, describe, expect, it } from "vitest";
import { gridCatalog, lookupField, resultsCatalog } from "../fieldCatalog";
import {
  gridExpressionMatchesRow,
  registerRowField,
  unregisterRowField,
} from "../gridScope";

const COLS = [
  { id: "c1", name: "customer_message", role: "input", data_type: "text" },
  { id: "c3", name: "language", role: "metadata", data_type: "text" },
];
const PROVIDERS = [
  { name: "language", type: "text", values: ["en", "es"] },
  { name: "customer_message", type: "text" },
  { name: "has_comment", type: "boolean" },
];

afterEach(() => {
  ["language", "has_comment"].forEach(unregisterRowField);
});

describe("W5 A6 · row fields yield to the dataset's names", () => {
  it("grid: the column keeps language and customer_message; has_comment is a provider", () => {
    const cat = gridCatalog({ columns: COLS, rowFields: PROVIDERS });
    expect(lookupField(cat, ["language"]).read.k).not.toBe("provider");
    expect(lookupField(cat, ["customer_message"]).read.k).toBe("input");
    expect(lookupField(cat, ["has_comment"]).read).toEqual({
      k: "provider",
      name: "has_comment",
    });
    expect(cat.fields.filter((f) => f.name === "language")).toHaveLength(1);
  });

  it("results: a metadata key takes the name too, so grid and results agree", () => {
    const withKey = resultsCatalog({
      inputColumns: [COLS[0]],
      metadataKeys: ["language"],
      rowFields: PROVIDERS,
    });
    expect(lookupField(withKey, ["language"])).toBeFalsy();
    expect(lookupField(withKey, ["metadata", "language"]).read.k).toBe(
      "metadata",
    );
    expect(lookupField(withKey, ["has_comment"]).read.k).toBe("provider");
    const withoutKey = resultsCatalog({
      inputColumns: [COLS[0]],
      rowFields: PROVIDERS,
    });
    expect(lookupField(withoutKey, ["language"]).read.k).toBe("provider");
  });

  it("a registered provider is read per row where the name is free", () => {
    registerRowField("has_comment", {
      type: "boolean",
      read: ({ rowId }) => rowId === "r2",
    });
    const rows = [
      { id: "r1", n: 1, values: { c1: "hi", c3: "en" } },
      { id: "r2", n: 2, values: { c1: "hola", c3: "es" } },
    ];
    const ctx = { columns: COLS };
    const ids = (e) =>
      rows.filter((r) => gridExpressionMatchesRow(e, r, ctx)).map((r) => r.id);
    expect(ids("has_comment")).toEqual(["r2"]);
    expect(ids("not has_comment")).toEqual(["r1"]);
    expect(ids('language = "es" and has_comment')).toEqual(["r2"]);
  });
});

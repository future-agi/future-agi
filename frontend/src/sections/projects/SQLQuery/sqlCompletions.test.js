import { describe, expect, it } from "vitest";
import { getSQLCompletions, quoteIdentifier } from "./sqlCompletions";

const datasets = [
  {
    name: "spans",
    description:
      "One live span in the selected start-time window; no prompt or output payloads.",
    columns: [
      { name: "name", type: "String" },
      { name: "trace_id", type: "String" },
      { name: "cost", type: "Float64" },
    ],
  },
  {
    name: "traces",
    columns: [
      { name: "id", type: "UUID" },
      { name: "external_id", type: "Nullable(String)" },
    ],
  },
  {
    name: "users",
    columns: [{ name: "user_id_type", type: "Nullable(String)" }],
  },
  {
    name: "derived_metrics",
    available: false,
    columns: [{ name: "value", type: "Nullable(Float64)" }],
  },
];
const complete = (marked, catalog = datasets) => {
  const offset = marked.indexOf("|");
  return getSQLCompletions(marked.replace("|", ""), offset, catalog);
};
const fields = (marked) =>
  complete(marked).suggestions.filter((item) => item.kind === "Field");
const labels = (items) => items.map((item) => item.label);

describe("catalog-backed SQL completion", () => {
  it.each([
    "SELECT * FROM |",
    "SELECT *\nFROM |",
    "SELECT * FROM spans s LEFT JOIN |",
    "SELECT * FROM spans, |",
    "select * from |",
  ])("offers only available datasets at %s", (query) => {
    expect(labels(complete(query).suggestions)).toEqual([
      "spans",
      "traces",
      "users",
    ]);
    expect(complete(query).suggestions[0]).toMatchObject({
      insertText: "`spans`",
      documentation: datasets[0].description,
    });
  });
  it.each([
    "SELECT | FROM spans",
    "SELECT * FROM spans |",
    "SELECT * FROM spans WHERE |",
    "SELECT * FROM spans GROUP BY |",
    "SELECT * FROM spans ORDER BY |",
    "SELECT * FROM spans HAVING |",
    "SELECT sum(|) FROM spans",
  ])("offers only in-scope columns at %s", (query) => {
    expect(labels(fields(query))).toEqual(["name", "trace_id", "cost"]);
    expect(fields(query)[0]).toMatchObject({
      insertText: "`name`",
      detail: "spans · String",
    });
  });
  it.each([
    "SELECT s.| FROM spans s",
    "SELECT s.| FROM spans AS s",
    "SELECT * FROM spans s WHERE s.|",
    "SELECT sum(s.|) FROM spans s",
    "SELECT `s`.| FROM `spans` AS `s`",
    'SELECT "s".| FROM "spans" AS "s"',
    "SELECT `;`.| FROM spans AS `;`",
    "SELECT `a``b`.| FROM spans AS `a``b`",
    "SELECT `a\\`b`.| FROM spans AS `a\\`b`",
  ])("resolves an active alias at %s", (query) => {
    const result = complete(query).suggestions;
    expect(labels(result)).toEqual(["name", "trace_id", "cost"]);
    expect(result.every((item) => item.kind === "Field")).toBe(true);
    expect(result[0].insertText).toBe("`name`");
  });
  it("qualifies unqualified columns when multiple relations are in scope", () => {
    const result = fields(
      "SELECT | FROM spans s JOIN traces t ON s.trace_id = t.id",
    );
    expect(result.map((item) => item.insertText)).toEqual([
      "`s`.`name`",
      "`s`.`trace_id`",
      "`s`.`cost`",
      "`t`.`id`",
      "`t`.`external_id`",
    ]);
  });
  it("offers the joined relation's columns after alias-dot", () => {
    expect(
      labels(
        complete("SELECT * FROM spans s JOIN traces t ON t.|").suggestions,
      ),
    ).toEqual(["id", "external_id"]);
    expect(
      labels(
        complete(
          "SELECT traces.| FROM spans JOIN traces ON spans.trace_id = traces.id",
        ).suggestions,
      ),
    ).toEqual(["id", "external_id"]);
  });
  it.each([
    "SELECT m.| FROM derived_metrics m",
    "SELECT missing.| FROM spans",
    "SELECT * FROM derived_metrics WHERE |",
    "SELECT * FROM physical.spans WHERE |",
  ])("never fabricates columns at %s", (query) => {
    expect(fields(query)).toEqual([]);
  });
  it.each([
    "SELECT 'FROM users JOIN traces' FROM spans WHERE |",
    "SELECT * FROM spans -- JOIN users\nWHERE |",
    "SELECT * FROM spans /* JOIN traces */ WHERE |",
    "SELECT * FROM spans /* nested /* comment */ JOIN traces */ WHERE |",
    "SELECT $doc$ FROM users JOIN traces $doc$ FROM spans WHERE |",
    "SELECT $$ FROM users $$ FROM spans WHERE |",
    "SELECT * FROM spans # JOIN users\nWHERE |",
    "SELECT * FROM spans #\nWHERE |",
    "SELECT * FROM spans #! JOIN users\nWHERE |",
    "SELECT 'escaped \\' FROM users' FROM spans WHERE |",
    "SELECT 'it''s FROM users' FROM spans WHERE |",
  ])("ignores string/comment relation text in %s", (query) => {
    expect(labels(fields(query))).toEqual(["name", "trace_id", "cost"]);
  });
  it.each([
    "SELECT 'FR|OM spans'",
    "SELECT 'FROM spans|",
    "SELECT * -- JOIN |\nFROM spans",
    "SELECT * -- FROM |",
    "SELECT * # FROM |",
    "SELECT * #! FROM |\nFROM spans",
    "SELECT /* FROM | */ * FROM spans",
    "SELECT /* FROM |",
    "SELECT /* nested /* comment */ FROM | */ * FROM spans",
    "SELECT $doc$ FROM | $doc$ FROM spans",
    "SELECT $$ FROM |",
    "SELECT $doc$ FROM |",
  ])("returns no suggestions inside opaque text in %s", (query) => {
    expect(complete(query).suggestions).toEqual([]);
  });
  it("uses multiline cursor offsets and replaces only the partial identifier", () => {
    const marked = "SELECT s.na|me\nFROM spans s";
    const result = complete(marked);
    expect(labels(result.suggestions)).toEqual(["name"]);
    expect(marked.replace("|", "").slice(result.from, result.to)).toBe("name");
    expect(result.suggestions[0].insertText).toBe("`name`");
  });
  it("replaces complete quoted tokens without doubling their quotes", () => {
    for (const text of [
      "SELECT s.`na|me` FROM spans s",
      'SELECT s."na|me" FROM spans s',
    ]) {
      const result = complete(text);
      const sql = text.replace("|", "");
      expect(
        sql.slice(0, result.from) +
          result.suggestions[0].insertText +
          sql.slice(result.to),
      ).toBe("SELECT s.`name` FROM spans s");
    }
  });
  it.each(["", "`", '"'])(
    "keeps filtering text and whole-token replacement aligned for delimiter %j",
    (delimiter) => {
      for (const [marked, label, expected] of [
        [
          `SELECT s.${delimiter}na|me${delimiter}\nFROM spans s`,
          "name",
          "SELECT s.`name`\nFROM spans s",
        ],
        [
          `SELECT * FROM ${delimiter}sp|ans${delimiter} s`,
          "spans",
          "SELECT * FROM `spans` s",
        ],
      ]) {
        const sql = marked.replace("|", "");
        const result = complete(marked);
        expect(result.suggestions).toHaveLength(1);
        expect(result.suggestions[0]).toMatchObject({
          label,
          filterText: `${delimiter}${label}${delimiter}`,
        });
        expect(sql.slice(result.from, result.to)).toBe(
          `${delimiter}${label}${delimiter}`,
        );
        expect(
          sql.slice(0, result.from) +
            result.suggestions[0].insertText +
            sql.slice(result.to),
        ).toBe(expected);
      }
    },
  );
  it.each(["`", '"'])(
    "handles incomplete, empty and already-closed %s identifiers",
    (delimiter) => {
      for (const token of ["na|", "|", "|name", "na|me", "name|"]) {
        for (const close of ["", delimiter]) {
          const marked = `SELECT * FROM spans s WHERE s.${delimiter}${token}${close}`;
          const sql = marked.replace("|", "");
          const result = complete(marked);
          const item = result.suggestions.find(
            (entry) => entry.label === "name",
          );
          expect(item.filterText).toBe(`${delimiter}name${delimiter}`);
          expect(
            sql.slice(0, result.from) + item.insertText + sql.slice(result.to),
          ).toBe("SELECT * FROM spans s WHERE s.`name`");
        }
      }
      const result = complete(
        `SELECT s.${delimiter}name${delimiter}| FROM spans s`,
      );
      expect(result.suggestions[0].filterText).toBe(
        `${delimiter}name${delimiter}`,
      );
    },
  );
  it.each([
    ["`a``|b`", "a`b", "`a``b`"],
    ["`a\\`|b`", "a`b", "`a\\`b`"],
    ['"a""|b"', 'a"b', '"a""b"'],
    ['"a\\"|b"', 'a"b', '"a\\"b"'],
    ["`a\\\\|b`", "a\\b", "`a\\\\b`"],
    ['"order |total"', "order total", '"order total"'],
  ])(
    "preserves the typed escape spelling for %s",
    (token, name, filterText) => {
      const marked = `SELECT s.${token} FROM spans s`;
      const sql = marked.replace("|", "");
      const result = complete(marked, [
        { name: "spans", columns: [{ name, type: "String" }] },
      ]);
      expect(result.suggestions).toHaveLength(1);
      expect(result.suggestions[0]).toMatchObject({
        label: name,
        filterText,
        insertText: quoteIdentifier(name),
      });
      expect(
        sql.slice(0, result.from) +
          result.suggestions[0].insertText +
          sql.slice(result.to),
      ).toBe(`SELECT s.${quoteIdentifier(name)} FROM spans s`);
    },
  );
  it("safely quotes catalog identifiers, including reserved words and escapes", () => {
    expect(quoteIdentifier("order")).toBe("`order`");
    expect(quoteIdentifier("a`b\\c")).toBe("`a\\`b\\\\c`");
    expect(
      complete("SELECT * FROM |", [{ name: "odd`name", columns: [] }])
        .suggestions[0].insertText,
    ).toBe("`odd\\`name`");
  });
  it("does not leak a subquery or another statement's fields into the active scope", () => {
    expect(
      labels(
        fields("SELECT | FROM spans WHERE trace_id IN (SELECT id FROM traces)"),
      ),
    ).toEqual(["name", "trace_id", "cost"]);
    expect(
      labels(
        fields("SELECT * FROM spans WHERE trace_id IN (SELECT | FROM traces)"),
      ),
    ).toEqual(["id", "external_id"]);
    expect(fields("SELECT * FROM spans; SELECT | ")).toEqual([]);
    expect(
      labels(fields("SELECT * FROM spans UNION ALL SELECT | FROM traces")),
    ).toEqual(["id", "external_id"]);
  });
  it("keeps unknown derived/CTE projections conservative", () => {
    expect(
      fields("WITH q AS (SELECT name FROM spans) SELECT q.| FROM q"),
    ).toEqual([]);
    expect(
      fields("WITH spans AS (SELECT id FROM traces) SELECT s.| FROM spans s"),
    ).toEqual([]);
    expect(fields("SELECT | FROM spans()")).toEqual([]);
  });
  it("offers useful SQL words/functions but no out-of-scope columns", () => {
    const result = complete("SELECT |").suggestions;
    expect(labels(result)).toEqual(
      expect.arrayContaining([
        "FROM",
        "WHERE",
        "GROUP BY",
        "ORDER BY",
        "count",
        "sum",
      ]),
    );
    expect(result.some((item) => item.kind === "Field")).toBe(false);
  });
});

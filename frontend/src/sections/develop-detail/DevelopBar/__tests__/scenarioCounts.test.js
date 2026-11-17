import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { scenarioCountsOf } from "../scenarioCounts";

const cols = [
  { id: "s", name: "scenario" },
  { id: "p", name: "persona" },
];
const row = (s, p) => ({
  s: { cell_value: s },
  p: { cell_value: JSON.stringify({ name: p, label: `${p} · calm` }) },
});

describe("scenarioCountsOf", () => {
  it("counts distinct scenarios and personas from the rows", () => {
    const table = [];
    ["A", "B"].forEach((s) =>
      ["Ana", "Raj", "Mike"].forEach((p) => table.push(row(s, p))),
    );
    expect(scenarioCountsOf({ column_config: cols, table })).toMatchObject({
      scenarios: 2,
      personas: 3,
      text: "2 scenarios × 3 personas",
    });
  });
  it("follows the data (one more scenario changes the line)", () => {
    const table = [row("A", "Ana"), row("B", "Ana"), row("C", "Ana")];
    expect(scenarioCountsOf({ column_config: cols, table }).text).toBe(
      "3 scenarios × 1 persona",
    );
  });
  it("is null without a scenario column or rows", () => {
    expect(
      scenarioCountsOf({ column_config: [], table: [row("A", "Ana")] }),
    ).toBeNull();
    expect(scenarioCountsOf({ column_config: cols, table: [] })).toBeNull();
  });
  it("puts no digit literal in its template (§C10)", () => {
    const src = fs.readFileSync(
      path.resolve(
        process.cwd(),
        "src/sections/develop-detail/DevelopBar/scenarioCounts.js",
      ),
      "utf8",
    );
    const templates = src.match(/`[^`]*`/g) ?? [];
    templates.forEach((t) =>
      expect(t.replace(/\$\{[^}]*\}/g, "")).not.toMatch(/[2-9]/),
    );
  });
});

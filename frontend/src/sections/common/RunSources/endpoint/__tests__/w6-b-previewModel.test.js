// W6 B2: the per-row request preview and its empty-variable counts.
import { describe, expect, it } from "vitest";
import { emptyLine, emptyVariables, previewOf } from "../previewModel";

const columns = [
  { id: "c1", name: "customer_message" },
  { id: "c2", name: "intent" },
];
const rows = [
  {
    row_id: "r1",
    order: 0,
    c1: { cellValue: "Where is A-7781?" },
    c2: { cellValue: "status" },
  },
  {
    row_id: "r2",
    order: 1,
    c1: { cellValue: "Refund please" },
    c2: { cellValue: "" },
  },
  {
    row_id: "r3",
    order: 2,
    c1: { cellValue: "Cancel" },
    c2: { cellValue: " " },
  },
];
const http = {
  url: "https://support-agent.acme.test/v2/reply",
  method: "POST",
  bodyTemplate:
    '{"message": "{{customer_message}}", "intent": "{{intent}}", "x": "{{ticket}}"}',
  headers: [{ name: "Authorization", secret: "FI_PROJECT_KEY" }],
};

describe("previewModel", () => {
  it("renders one row's request with secrets by reference", () => {
    const p = previewOf({ http, columns, row: rows[0] });
    expect(JSON.parse(p.body)).toEqual({
      message: "Where is A-7781?",
      intent: "status",
      x: "{{ticket}}",
    });
    expect(p.shownHeaders).toEqual({
      Authorization: "•••• (from secret FI_PROJECT_KEY)",
    });
  });

  it("counts empty and unsupplied variables over the loaded rows", () => {
    const gaps = emptyVariables({ http, columns, rows });
    expect(
      gaps.map((g) => [g.kind, g.name, g.rows.map((r) => r.row_id)]),
    ).toEqual([
      ["missing", "ticket", ["r1", "r2", "r3"]],
      ["empty", "intent", ["r2", "r3"]],
    ]);
    expect(emptyLine(gaps[1], 3, 20)).toBe(
      "{{intent}} is empty on 2 of the first 3 rows",
    );
    expect(emptyLine(gaps[1], 3, 3)).toBe("{{intent}} is empty on 2 of 3 rows");
  });
});

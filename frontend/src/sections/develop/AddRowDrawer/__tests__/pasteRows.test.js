import { describe, expect, it } from "vitest";
import { pasteFormat, pastedRows } from "../pasteRows";

const columns = [
  { id: "c1", name: "customer_message" },
  { id: "c3", name: "expected_reply" },
];

describe("paste rows (IT-R1 G5 P3)", () => {
  it("detects the format", () => {
    expect(pasteFormat("a,b\n1,2")).toBe("csv");
    expect(pasteFormat("a\tb\n1\t2")).toBe("tsv");
    expect(pasteFormat('[{"a":1}]')).toBe("json");
    expect(pasteFormat('{"a":1}\n{"a":2}')).toBe("jsonl");
  });

  it("maps headers to column ids by name and skips the rest", () => {
    const out = pastedRows(
      'customer_message,expected_reply,foo\n"Where is A-1, please?",Tomorrow,x',
      columns,
    );
    expect(out.rows).toEqual([{ c1: "Where is A-1, please?", c3: "Tomorrow" }]);
    expect(out.matched).toEqual(["customer_message", "expected_reply"]);
    expect(out.ignored).toEqual(["foo"]);
  });

  it("reads spreadsheet (TSV) and JSON lines pastes", () => {
    expect(pastedRows("Customer_Message\nhi", columns).rows).toEqual([
      { c1: "hi" },
    ]);
    expect(
      pastedRows("customer_message\texpected_reply\nhi\tyo", columns).rows,
    ).toEqual([{ c1: "hi", c3: "yo" }]);
    expect(
      pastedRows('{"customer_message":"a"}\n{"customer_message":"b"}', columns)
        .rows,
    ).toEqual([{ c1: "a" }, { c1: "b" }]);
  });
});

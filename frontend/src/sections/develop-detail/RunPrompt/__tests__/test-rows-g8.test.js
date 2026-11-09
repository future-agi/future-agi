// v2.1 g8 (knob:run-prompt:test-preview): Test cards are in grid order with
// `Row N` labels, so card 1 is the reply Apply and Run writes to row 1.
import { describe, expect, it } from "vitest";
import { testRowsOf } from "../testRows";

describe("Run Prompt Test rows", () => {
  it("keeps the server's order and labels each row by its number", () => {
    const out = testRowsOf({
      data: {
        result: {
          responses: ["r1", "r2", "r3"],
          rows: [{ row_number: 1 }, { row_number: 2 }, { row_number: 3 }],
        },
      },
    });
    expect(out.map((x) => x.response)).toEqual(["r1", "r2", "r3"]);
    expect(out.map((x) => x.rowLabel)).toEqual(["Row 1", "Row 2", "Row 3"]);
  });

  it("picked rows keep their own numbers", () => {
    const out = testRowsOf({
      data: {
        result: {
          responses: ["a", "b"],
          rows: [{ row_number: 7 }, { row_number: 12 }],
        },
      },
    });
    expect(out.map((x) => x.rowLabel)).toEqual(["Row 7", "Row 12"]);
  });

  it("falls back to positions and to nothing", () => {
    expect(
      testRowsOf({ data: { result: { responses: ["x"] } } })[0].rowLabel,
    ).toBe("Row 1");
    expect(testRowsOf(undefined)).toEqual([]);
  });
});

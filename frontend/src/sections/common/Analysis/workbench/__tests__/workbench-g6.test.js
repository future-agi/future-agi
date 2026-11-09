// L7c g6: Explore group-by names, totals and placeholders.
import { describe, expect, it } from "vitest";
import { railPlaceholders } from "../railPlaceholders";
import { evalNameOf } from "../workbenchPivot";

describe("Explore group-by (L7c g6)", () => {
  it("names a failed eval, never its raw key", () => {
    expect(
      evalNameOf("eval:correctness", {
        metric_names: { "eval:correctness": "Correctness" },
      }),
    ).toBe("Correctness");
    expect(evalNameOf("check:order_found", {})).toBe("Order found");
    expect(evalNameOf("eval:correctness", null)).toBe("Correctness");
  });
  it("placeholders name this dataset's own column", () => {
    expect(railPlaceholders([{ name: "question" }]).expression).toBe(
      "e.g. len(question) > 400",
    );
    expect(railPlaceholders([]).path).toBe("e.g. column.field[0]");
    expect(railPlaceholders(["expected_tool_calls"]).path).toMatch(
      /expected_tool_calls/,
    );
  });
});

import { describe, expect, it } from "vitest";
import { rowSummaryParts } from "../rowSummary";

const f = (data_type, cell_value) => ({
  column: { data_type },
  cell: { cell_value },
});

describe("rowSummaryParts (IT-R1 G5 P10)", () => {
  it("says what the row holds in one line", () => {
    const model = {
      input: [
        f(
          "json",
          JSON.stringify([
            {
              role: "assistant",
              tool_calls: [{ function: { name: "lookup_order" } }],
            },
            {
              role: "assistant",
              tool_calls: [{ function: { name: "refund" } }],
            },
          ]),
        ),
        f("images", JSON.stringify(["a.png", "b.png"])),
        f("audio", "call.mp3"),
        f("text", "https://x/contract.pdf"),
      ],
      expected: [],
      metadata: [f("json", { customer: { tier: "enterprise" } })],
      noRole: [],
    };
    expect(rowSummaryParts(model)).toEqual([
      "tier enterprise",
      "2 tool calls",
      "2 images",
      "1 audio",
      "1 PDF",
    ]);
  });

  it("is empty for a plain text row", () => {
    expect(
      rowSummaryParts({
        input: [f("text", "hi")],
        expected: [],
        metadata: [],
        noRole: [],
      }),
    ).toEqual([]);
  });
});

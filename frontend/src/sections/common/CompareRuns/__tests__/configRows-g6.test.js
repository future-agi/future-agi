// L7c g6 (cfg:diff / cmp:task-setup-diff): one row per setting; lists as names.
import { describe, expect, it } from "vitest";
import { changeSignature, listSideText } from "../configRows";

describe("config rows (L7c g6)", () => {
  it("reads tool lists as names, an empty list as none", () => {
    expect(
      listSideText(["lookup_order", "issue_refund", "escalate_to_human"]),
    ).toBe("lookup_order, issue_refund, escalate_to_human");
    expect(listSideText([{ name: "search_kb" }])).toBe("search_kb");
    expect(listSideText([])).toBe("none");
  });
  it("one signature for repeated chips of the same change", () => {
    const chip = (text) => ({
      axis: "tools",
      text,
      detail: { from: ["a", "b"], to: [] },
    });
    expect(changeSignature(chip("-a"))).toBe(changeSignature(chip("-b")));
    expect(changeSignature({ axis: "model", text: "x" })).toBeNull();
  });
});

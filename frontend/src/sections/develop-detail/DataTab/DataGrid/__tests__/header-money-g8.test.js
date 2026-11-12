// v2.1 g8 (dyncol:retrieval / extract entities): a paid column total under
// a cent reads `<$0.01`, never `$0.00`.
import { describe, expect, it } from "vitest";
import { money } from "../dataGridView";

describe("header money", () => {
  it("sub-cent totals", () => {
    expect(money(0.00496)).toBe("$0.00496");
    expect(money(0.0000012)).toBe("$0.0000012");
  });
  it("zero and cents", () => {
    expect(money(0)).toBe("$0.00");
    expect(money(0.08)).toBe("$0.08");
    expect(money(0.006)).toBe("$0.006");
  });
});

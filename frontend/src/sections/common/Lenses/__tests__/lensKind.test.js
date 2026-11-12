import { describe, expect, it } from "vitest";
import { CALL_VALUE_EXAMPLE } from "src/test-fixtures/call-value";
import { lensKindOf } from "../lensKind";

describe("lensKindOf", () => {
  it("call beats media beats json beats text", () => {
    expect(lensKindOf([CALL_VALUE_EXAMPLE, "data:image/png;base64,AA"])).toBe(
      "call",
    );
    expect(lensKindOf(["data:image/png;base64,AA", '{"a":1}'])).toBe("image");
    expect(lensKindOf(['{"warranty_months": 24}', "text"])).toBe("json");
    expect(lensKindOf(["plain", "text"])).toBeNull();
    expect(lensKindOf([])).toBeNull();
  });
  it("picks the most common media kind", () => {
    expect(lensKindOf(["/c/contract.pdf", "/c/other.pdf", "/i/a.png"])).toBe(
      "document",
    );
    expect(lensKindOf(["/a/x.wav"])).toBe("audio");
    expect(lensKindOf(["/v/x.mp4"])).toBe("video");
  });
  it("treats conversations as text (R3 views)", () => {
    expect(lensKindOf([[{ role: "user", content: "hi" }]])).toBeNull();
  });
});

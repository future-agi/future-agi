import { describe, expect, it } from "vitest";
import { parseTimeoutSeconds } from "./utils";

describe("parseTimeoutSeconds", () => {
  it("normalizes Gateway provider timeout text to integer seconds", () => {
    expect(parseTimeoutSeconds("45")).toBe(45);
    expect(parseTimeoutSeconds("45s")).toBe(45);
    expect(parseTimeoutSeconds("2m")).toBe(120);
    expect(parseTimeoutSeconds("1500ms")).toBe(2);
    expect(parseTimeoutSeconds("")).toBeNull();
    expect(parseTimeoutSeconds("soon")).toBeNull();
    expect(parseTimeoutSeconds("0s")).toBeNull();
  });
});

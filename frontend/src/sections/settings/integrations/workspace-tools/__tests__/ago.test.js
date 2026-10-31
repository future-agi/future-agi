import { describe, expect, it } from "vitest";
import { ago } from "../coding-agents/ago";

describe("ago", () => {
  it("shows nothing when there is no date", () => {
    expect(ago(undefined)).toBe("");
  });
});

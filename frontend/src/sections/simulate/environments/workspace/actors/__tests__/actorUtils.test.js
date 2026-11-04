import { describe, expect, it } from "vitest";
import { hidden, idFrom, idRule, readable } from "../actorUtils";

describe("actorUtils", () => {
  it("makes an id from a name, folding accents and staying unique in the cast", () => {
    expect(idFrom("Sam Lee", new Set())).toBe("sam_lee");
    expect(idFrom("Sam Lee", new Set(["sam_lee", "sam_lee_2"]))).toBe(
      "sam_lee_3",
    );
    expect(idFrom("Zoë  O'Brien", new Set())).toBe("zoe_o_brien");
    expect(idFrom("", new Set())).toBe("actor");
    expect(idFrom("!!!", new Set())).toBe("actor");
    expect(idFrom("x".repeat(80), new Set())).toHaveLength(60);
  });

  it("reads the id rule from the record schemas, and none when they send none", () => {
    const rule = idRule({
      "actors.json": { properties: { id: { pattern: "^[a-z_]{1,4}$" } } },
    });
    expect([rule.test("ab_c"), rule.test("abcde"), rule.test("A")]).toEqual([
      true,
      false,
      false,
    ]);
    expect(idRule({})).toBeNull();
    expect(idRule(undefined)).toBeNull();
  });

  it("reads a view value as words, never raw JSON", () => {
    expect(readable(null)).toBe("");
    expect(readable(0)).toBe("0");
    expect(readable(["a", "", "b"])).toBe("a; b");
    expect(readable({ kind: "facts", text: "The limit is 25%" })).toBe(
      "The limit is 25%",
    );
    expect(
      readable({
        kind: "records",
        response_timing: { kind: "delayed", seconds: 5 },
      }),
    ).toBe("records · response timing: delayed · seconds: 5");
  });

  it("groups what an actor never sees into grader material and other actors' fields", () => {
    expect(
      hidden([
        "scenario.sub_goals",
        "file.sub_goals.json",
        "colleague.goal",
        "colleague.prompt",
        "boss.goal",
        "boss.prompt",
      ]),
    ).toEqual({
      grader: ["sub goals", "sub_goals.json"],
      others: [["goal, prompt", 2]],
    });
    expect(hidden(undefined)).toEqual({ grader: [], others: [] });
  });
});

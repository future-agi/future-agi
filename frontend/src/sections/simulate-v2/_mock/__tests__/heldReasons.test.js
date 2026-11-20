import { describe, expect, it } from "vitest";
import { heldReasonOf, nameList } from "../heldReasons";

describe("heldReasonOf", () => {
  const rows = [
    {
      label: "Streaming",
      repair: "connection",
      blocks: true,
      affected: ["a"],
      detail: "lookup_order: not done: get_order was not called successfully",
    },
    { label: "Cancel", repair: "agent", blocks: false, affected: ["a"] },
  ];

  it("leads with a plain sentence and keeps the check's words as detail", () => {
    const r = heldReasonOf({ id: "a" }, rows);
    expect(r.plain).toBe(
      "The agent couldn't handle streaming when we last checked it.",
    );
    expect(r.detail).toMatch(/get_order was not called/);
  });

  it("falls back to a plain line when no row explains it", () => {
    expect(heldReasonOf({ id: "b" }, rows).plain).toMatch(/can't run/);
  });
});

describe("nameList", () => {
  it("joins names without doubled full stops", () => {
    expect(nameList(["One.", "Two."])).toBe("One and Two");
    expect(nameList(["A", "B", "C", "D", "E"])).toBe("A, B, C and 2 more");
  });
});

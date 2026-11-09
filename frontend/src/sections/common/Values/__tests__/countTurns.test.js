import { describe, expect, it } from "vitest";
import countTurns from "../countTurns";

describe("countTurns", () => {
  it("counts user and assistant messages only", () => {
    expect(
      countTurns([
        { role: "system", content: "policy" },
        { role: "user", content: "hello" },
        { role: "assistant", content: null, tool_calls: [] },
        { role: "tool", content: "result" },
        { role: "assistant", content: "done" },
      ]),
    ).toBe(3);
  });

  it("accepts the normalized customer and agent roles", () => {
    expect(
      countTurns([{ role: "customer" }, { role: "agent" }, { role: "tool" }]),
    ).toBe(2);
  });
});

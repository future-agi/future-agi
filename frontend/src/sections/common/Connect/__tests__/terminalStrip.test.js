// IT-R1 G1 P5: a run from the user's terminal reads the run's own status.
import { describe, expect, it } from "vitest";
import { stripLine, terminalStripLine } from "../connectCopy";

describe("terminal strip (IT-R1 G1 P5)", () => {
  const a = { terminal: true, total: 24 };
  it("reads waiting · running · done from the run", () => {
    expect(stripLine({ ...a, state: "waiting", done: 0 })).toBe(
      "Waiting for your terminal · 0/24",
    );
    expect(
      terminalStripLine({
        ...a,
        state: "running",
        done: 9,
        passed: 5,
        failed: 4,
      }),
    ).toBe("Running on your machine · 9/24 · 5 passed · 4 failed");
    expect(
      stripLine({ ...a, state: "done", done: 24, passed: 13, failed: 11 }),
    ).toBe("Finished 24/24 · 13 passed · 11 failed · ran on your machine");
  });
});

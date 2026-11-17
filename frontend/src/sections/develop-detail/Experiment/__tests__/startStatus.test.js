import { describe, expect, it } from "vitest";
import { startStatusLine } from "../StartItBlock";

describe("IT-R2 G1 P2 · Review's Start it: one status line per run", () => {
  it("not started / simulated / harness", () => {
    expect(startStatusLine({ kind: "code" }, null)).toBe(
      "not started · nothing runs until you start it",
    );
    expect(startStatusLine({ kind: "harness" }, null)).toBe(
      "not started · runs when you start its command",
    );
    expect(
      startStatusLine(
        { kind: "code", arrival: {} },
        { draft: true, done: 7, total: 24, state: "running" },
      ),
    ).toBe("simulated 7/24 · will continue after Run experiment");
    expect(
      startStatusLine(
        { kind: "code", arrival: {} },
        {
          draft: true,
          done: 24,
          total: 24,
          state: "done",
          passed: 13,
          failed: 11,
        },
      ),
    ).toBe(
      "simulated 24/24 · 13 passed · 11 failed · results open after Run experiment",
    );
  });
});

describe("IT-R4 G1 P5 · Start it says where each run starts", () => {
  it("names CI and machine runs; never 'Every run…' when one is CI", async () => {
    const { startWhereLine, ciRunsOf, terminalRunsOf } = await import(
      "../StartItBlock"
    );
    const cards = [
      { r4: { kind: "harness", mode: "ci", entryName: "Claude Code" } },
      { r4: { kind: "harness", mode: "local", entryName: "Codex CLI" } },
      {
        r4: {
          kind: "code",
          mode: "local",
          form: "upload",
          entryId: "custom_typescript",
          entryName: "Custom TypeScript",
        },
      },
    ];
    const runs = cards.map((_, i) => ({ configIndex: i, letter: "ABC"[i] }));
    // dataset integration FE-0 (review must-fix 2 backstop): a code or harness card is
    // not an offered source, so neither list takes it (no Start it block).
    expect(terminalRunsOf(cards, runs)).toEqual([]);
    expect(ciRunsOf(cards, runs)).toEqual([]);
    // The sentence builder itself, on lists given to it directly.
    const machine = [runs[1], runs[2]].map((run) => ({
      run,
      r4: cards[run.configIndex].r4,
    }));
    const ci = [{ run: runs[0], r4: cards[0].r4 }];
    expect(startWhereLine(machine, ci, 3)).toBe(
      "Claude Code runs in CI (GitHub Actions) · Codex CLI and your TypeScript agent run on this machine",
    );
    expect(startWhereLine(machine, [], 2)).toBe(
      "Every run starts on your machine; each row lands here as it finishes.",
    );
  });
});

// options-truth L2 G3 (S5): live counters say rows with one try and tries
// with more, uncapped, with the grid's pass % once the run ends.
import { describe, expect, it } from "vitest";
import {
  countsTextOf,
  liveCountsOf,
  playbackTip,
  stripLine,
  terminalStripLine,
} from "../connectCopy";

const act = (o) => ({
  terminal: true,
  row_noun: "task",
  trials: 1,
  rows_total: 30,
  rows_done: 30,
  tries_total: 30,
  tries_done: 30,
  tries_passed: 23,
  tries_failed: 7,
  flaky_rows: 0,
  state: "done",
  ...o,
});

describe("terminal strip counts", () => {
  it("one try: rows and passed / failed", () => {
    expect(terminalStripLine(act())).toBe(
      "Finished 30/30 · 23 passed · 7 failed · ran on your machine",
    );
  });
  it("tries > 1: never capped at the row count; % and flaky at the end", () => {
    const a = act({
      trials: 2,
      tries_total: 60,
      tries_done: 60,
      tries_passed: 47,
      tries_failed: 13,
      flaky_rows: 9,
      pass_pct: 78.3,
    });
    expect(terminalStripLine(a)).toBe(
      "Finished 30/30 tasks × 2 tries · 47/60 tries passed (78.3%) · 9 flaky · ran on your machine",
    );
    expect(terminalStripLine({ ...a, state: "running", rows_done: 13 })).toBe(
      "Running on your machine · 13/30 tasks × 2 tries · 47/60 tries passed",
    );
  });
  it("a cap stop names the cap and the rows left", () => {
    const a = act({
      state: "stopped",
      rows_done: 11,
      tries_done: 11,
      tries_passed: 9,
      tries_failed: 2,
      rows_left: 19,
      stop_reason: "Stopped at cost cap",
      cap_usd: 0.9,
    });
    expect(terminalStripLine(a)).toBe(
      "Stopped at cost cap $0.90 · 11/30 · 9 passed · 2 failed · 19 tasks left",
    );
  });
  it("a connected machine's strip reads the same counts", () => {
    const a = act({
      terminal: false,
      machine: { name: "alice@mbp-14" },
      slots: 4,
      state: "running",
      trials: 3,
      rows_done: 13,
      tries_passed: 34,
      tries_failed: 5,
    });
    expect(stripLine(a)).toBe(
      "Running on alice@mbp-14 · 4 at a time · 13/30 tasks × 3 tries · 34/39 tries passed",
    );
    expect(countsTextOf({})).toBeNull();
  });
});

describe("drawer counters", () => {
  it("checklist and footer count tries when tries > 1, never `of 1`", () => {
    const live = liveCountsOf(
      act({
        trials: 3,
        rows_done: 17,
        tries_total: 90,
        tries_done: 39,
        tries_passed: 34,
        tries_failed: 5,
      }),
      { rowNoun: "task" },
    );
    expect(live.checklist).toBe(
      "Tries 39 of 90 · 34 passed · 5 failed · 17/30 tasks",
    );
    const end = liveCountsOf(
      act({
        trials: 3,
        tries_total: 90,
        tries_done: 90,
        tries_passed: 84,
        tries_failed: 6,
      }),
      { rowNoun: "task" },
    );
    expect(end.footer).toBe(
      "Results in · 90 of 90 tries · 84 passed · 6 failed",
    );
    expect(
      liveCountsOf(act({ rows_done: 12, tries_passed: 9, tries_failed: 3 }), {
        rowNoun: "task",
      }).checklist,
    ).toBe("Tasks 12 of 30 · 9 passed · 3 failed");
  });
  it("the Simulated playback tip names the speed", () => {
    expect(playbackTip(20)).toBe("Plays back 20× faster than real time");
    expect(playbackTip(1)).toBe(
      "Plays back at the pace the run would really take",
    );
  });
});

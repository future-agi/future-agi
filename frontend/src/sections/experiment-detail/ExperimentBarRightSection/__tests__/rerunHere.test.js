// dataset integration FE-0 (owner Q12) on the experiment header's Rerun menu: the runs it
// offers are read with their configs, so a code / harness / voice / upload
// run is never offered (the bare experiment run list has no kind).
import { describe, expect, it } from "vitest";
import { allItemLabel, rerunHereOf } from "../rerunHere";

const bare = (id, label) => ({ run_id: id, label });
const cfg = (id, kind) => ({ run_id: id, config: { kind } });

describe("rerunHereOf", () => {
  it("reads each run's kind from the config lists, not the bare run", () => {
    const runs = [bare("a", "#3A"), bare("e", "#3E"), bare("f", "#3F")];
    const out = rerunHereOf(runs, [
      [cfg("a", "code"), cfg("e", "harness"), cfg("f", "harness")],
    ]);
    expect(out.here).toEqual([]);
    expect(out.allHere).toBe(false);
    expect(out.changesRun).toBeNull();
    expect(out.runs.map((r) => r.config.kind)).toEqual([
      "code",
      "harness",
      "harness",
    ]);
  });

  it("keeps prompt, agent and endpoint runs; the first list with a config wins", () => {
    const runs = [
      bare("p", "#2A"),
      bare("g", "#2B"),
      bare("h", "#2C"),
      bare("u", "#2D"),
      bare("v", "#2E"),
    ];
    const out = rerunHereOf(runs, [
      [cfg("p", "prompt"), cfg("g", "agent"), cfg("v", "voice")],
      [cfg("h", "http"), cfg("u", "upload"), cfg("p", "code")],
    ]);
    expect(out.here.map((r) => r.label)).toEqual(["#2A", "#2B", "#2C"]);
    expect(out.allHere).toBe(false);
    expect(out.runs[0].config.kind).toBe("prompt");
  });

  it("Rerun with changes starts from the candidate when it can be rerun, else the newest that can", () => {
    const runs = [bare("p1", "#1A"), bare("p2", "#1B"), bare("c", "#1C")];
    const lists = [[cfg("p1", "prompt"), cfg("p2", "agent"), cfg("c", "code")]];
    expect(rerunHereOf(runs, lists, "p1").changesRun.label).toBe("#1A");
    expect(rerunHereOf(runs, lists, "c").changesRun.label).toBe("#1B");
    expect(rerunHereOf(runs, lists, null).changesRun.label).toBe("#1B");
  });

  it("all runs rerunnable: allHere, and a run without any config keeps its own", () => {
    const own = { run_id: "x", label: "#4A", config: { kind: "agent" } };
    const out = rerunHereOf([bare("p", "#4B"), own], [[cfg("p", "prompt")]]);
    expect(out.allHere).toBe(true);
    expect(out.runs[1]).toBe(own);
  });

  it("tolerates missing inputs", () => {
    expect(rerunHereOf(undefined, undefined)).toEqual({
      runs: [],
      here: [],
      allHere: false,
      changesRun: null,
    });
    expect(
      rerunHereOf([bare("p", "#5A")], [undefined, null]).here,
    ).toHaveLength(1);
  });
});

describe("allItemLabel", () => {
  it("names every run, or the ones it reruns", () => {
    expect(
      allItemLabel({ here: [bare("a", "#2A")], allHere: true }, "task"),
    ).toBe("All tasks, all runs");
    expect(
      allItemLabel(
        { here: [bare("a", "#2A"), bare("c", "#2C")], allHere: false },
        "row",
      ),
    ).toBe("All rows, #2A, #2C");
    expect(
      allItemLabel({
        here: [{ run_id: "a" }, { run_id: "b" }],
        allHere: false,
      }),
    ).toBe("All rows, 2 runs");
  });
});

// HO S7: the gate door. Only a `left_out` or `quick` gate offers
// `Run every row on v3.1`; it opens the dock on the run the fix started from
// (scope All) with exactly the fix's edits, saved under the same version.
import { describe, expect, it } from "vitest";
import {
  FULL_RUN_STATES,
  draftOfChanges,
  fullRunDoorOf,
  fullRunLabel,
  ranCandOf,
} from "src/sections/experiment-detail/iterate/fullRunDoor";
import { collectChanges } from "src/sections/experiment-detail/iterate/iterateModel";
import {
  draftEdits,
  editsKey,
} from "src/sections/experiment-detail/iterate/useIterateDock";
import { entryFromSearch } from "src/sections/experiment-detail/iterate/useIterateParams";
import { publishGateOf } from "src/sections/experiment-detail/iterate/publishGate";

const PARENT_CONFIG = {
  agent: {
    graph_id: "g1",
    version_id: "gv-3",
    version: "v3",
    nodes: [
      { id: "plan", prompt: "Plan." },
      { id: "classify_intent", prompt: "Classify the intent." },
    ],
    limits: { max_iterations: 12 },
  },
};
const CHANGES = [
  {
    axis: "agent",
    node: "classify_intent",
    field: "prompt",
    value: "Keep the prefix. Classify the intent.",
  },
  { axis: "agent", field: "max_iterations", value: 5 },
];
const cand = (over = {}) => ({
  run_id: "r-2h",
  label: "#2H",
  parent_run_id: "r-2b",
  changes: CHANGES,
  listed: true,
  config: { agent: { ...PARENT_CONFIG.agent, version_id: "gv-31" } },
  ...over,
});
const PATH = "/dashboard/develop/experiment/e2/data?tab=data";

describe("fullRunLabel", () => {
  it("names the version", () => {
    expect(fullRunLabel("v3.1")).toBe("Run every row on v3.1");
  });
});

describe("fullRunDoorOf", () => {
  it("shows only for left_out and quick", () => {
    expect(FULL_RUN_STATES).toEqual(["left_out", "quick"]);
    for (const state of [
      "published",
      "editor",
      "waiting",
      "carried",
      "ready",
    ]) {
      expect(
        fullRunDoorOf({
          gate: { state },
          cand: cand(),
          versionName: "v3.1",
          path: PATH,
        }),
      ).toBeNull();
    }
    expect(
      fullRunDoorOf({
        gate: null,
        cand: cand(),
        versionName: "v3.1",
        path: PATH,
      }),
    ).toBeNull();
  });

  it("reads the real gate: Leave them out and a quick test open the door", () => {
    const base = {
      rerunCmp: { partial: null },
      carriedFromBase: 0,
      baseRows: 30,
      draftVersion: { draft: true, version: "v3.1" },
      baseLabel: "#2B",
      candLabel: "#2H",
      canEdit: true,
    };
    const left = publishGateOf({ ...base, cand: cand(), notRun: 17 });
    expect(left.state).toBe("left_out");
    const quick = publishGateOf({
      ...base,
      cand: cand({ listed: false, kind: "quick_test" }),
      notRun: 17,
    });
    expect(quick.state).toBe("quick");
    for (const gate of [left, quick]) {
      const door = fullRunDoorOf({
        gate,
        cand: cand(),
        versionName: "v3.1",
        path: PATH,
      });
      expect(door.label).toBe("Run every row on v3.1");
    }
    const ready = publishGateOf({ ...base, cand: cand(), notRun: 0 });
    expect(ready.state).toBe("ready");
    expect(
      fullRunDoorOf({
        gate: ready,
        cand: cand(),
        versionName: "v3.1",
        path: PATH,
      }),
    ).toBeNull();
  });

  it("opens the dock on the run the fix started from, scope All, on the changed node", () => {
    const door = fullRunDoorOf({
      gate: { state: "left_out" },
      cand: cand(),
      versionName: "v3.1",
      path: PATH,
    });
    expect(door.baseId).toBe("r-2b");
    const [path, search] = door.href.split("?");
    expect(path).toBe("/dashboard/develop/experiment/e2/data");
    const entry = entryFromSearch(`?${search}`);
    expect(entry).toMatchObject({
      runId: "r-2b",
      scope: "all",
      node: "classify_intent",
    });
    expect(new URLSearchParams(search).get("tab")).toBe("data");
  });

  it("seeds exactly the fix's edits under the same version, nothing ran", () => {
    const door = fullRunDoorOf({
      gate: { state: "quick" },
      cand: cand(),
      versionName: "v3.1",
      path: PATH,
    });
    expect(door.draft).toEqual({
      prompts: { classify_intent: "Keep the prefix. Classify the intent." },
      messages: null,
      model: null,
      tools: null,
      harness: null,
      maxIterations: 5,
      saveName: "v3.1",
      ran: null,
    });
    // Replayed through the dock's own builders, the edits give back the
    // fix's changes (the one command builder sends them).
    const parent = { config: PARENT_CONFIG };
    const d = { ...door.draft, node: "classify_intent" };
    expect(collectChanges(PARENT_CONFIG, draftEdits(parent, d))).toEqual(
      CHANGES,
    );
  });

  it("stays away without a parent run, a version name or replayable changes", () => {
    const gate = { state: "left_out" };
    expect(
      fullRunDoorOf({
        gate,
        cand: cand({ parent_run_id: null }),
        versionName: "v3.1",
        path: PATH,
      }),
    ).toBeNull();
    expect(
      fullRunDoorOf({ gate, cand: cand(), versionName: null, path: PATH }),
    ).toBeNull();
    expect(
      fullRunDoorOf({
        gate,
        cand: cand({ changes: [] }),
        versionName: "v3.1",
        path: PATH,
      }),
    ).toBeNull();
    expect(
      fullRunDoorOf({
        gate,
        cand: cand({ changes: [{ axis: "dataset", field: "x", value: 1 }] }),
        versionName: "v3.1",
        path: PATH,
      }),
    ).toBeNull();
  });
});

describe("draftOfChanges", () => {
  it("inverts every axis collectChanges writes for an agent run", () => {
    const changes = [
      { axis: "agent", node: "plan", field: "prompt", value: "P2" },
      { axis: "model", field: "id", value: "gpt-4o" },
      { axis: "params", field: "temperature", value: 0.2 },
      { axis: "params", field: "max_tokens", value: 900 },
      { axis: "tools", node: "lookup_order", field: "mode", value: "off" },
      { axis: "tools", field: "max_tool_turns", value: 3 },
    ];
    expect(draftOfChanges(changes)).toEqual({
      node: "plan",
      prompts: { plan: "P2" },
      model: { id: "gpt-4o", temperature: 0.2, maxTokens: 900 },
      tools: { modes: { lookup_order: "off" }, maxToolTurns: 3 },
    });
    expect(draftOfChanges([])).toEqual({});
    expect(draftOfChanges(null)).toEqual({});
    expect(
      draftOfChanges([{ axis: "prompt", field: "messages.0", value: "x" }]),
    ).toBeNull();
  });
});

describe("ranCandOf (a quick test is off R1)", () => {
  it("rebuilds the run that just ran from the dock's own snapshot", () => {
    const parent = { run_id: "r-2b", config: PARENT_CONFIG };
    const draft = {
      node: "classify_intent",
      prompts: { classify_intent: CHANGES[0].value },
      maxIterations: 5,
    };
    const ran = { runId: "r-q", edits: editsKey(parent, draft), name: "v3.1" };
    expect(ranCandOf(parent, ran)).toEqual({
      run_id: "r-q",
      parent_run_id: "r-2b",
      changes: CHANGES,
    });
    const door = fullRunDoorOf({
      gate: { state: "quick" },
      cand: ranCandOf(parent, ran),
      versionName: ran.name,
      path: PATH,
    });
    expect(door.baseId).toBe("r-2b");
    expect(door.label).toBe("Run every row on v3.1");
  });

  it("is null without a parent or a snapshot", () => {
    expect(ranCandOf(null, { edits: "{}" })).toBeNull();
    expect(ranCandOf({ run_id: "r" }, null)).toBeNull();
    expect(ranCandOf({ run_id: "r" }, { edits: "not json" })).toBeNull();
  });
});

// Error Feed split-view pins (R4 lane T). Written BEFORE lane T's additive
// diff statuses landed in AgentGraph: the Error Feed's annotated graph
// (buildGraphDiff) must flow through buildFlowData exactly as before, and
// every existing diff status keeps its overlay. Lane T's new statuses are
// data-gated (`_diffColor`, `_visits`, `_loops`, edge `_diffStatus`), so a
// graph without them renders byte for byte as before.
import { describe, expect, it } from "vitest";
import { buildFlowData, getDiffOverlay } from "../AgentGraph";
import { buildGraphDiff } from "src/pages/dashboard/error-feed/components/buildGraphDiff";
// DS v0.2 intent: 8c05b554c9 (via adae445253) makes error.main the semantic red.
import { error as errorPalette } from "src/theme/palette";

const node = (id, type = "tool", extra = {}) => ({
  id,
  name: id,
  type,
  span_count: 1,
  avg_latency_ms: 10,
  total_tokens: 0,
  total_cost: 0,
  error_count: 0,
  trace_count: 1,
  ...extra,
});
const edge = (source, target, extra = {}) => ({
  source,
  target,
  transition_count: 1,
  avg_latency_ms: 0,
  total_tokens: 0,
  total_cost: 0,
  error_count: 0,
  trace_count: 1,
  is_self_loop: source === target,
  ...extra,
});

const pass = {
  nodes: [node("__start__", "start"), node("a"), node("b"), node("c")],
  edges: [edge("__start__", "a"), edge("a", "b"), edge("a", "c")],
};
const fail = {
  nodes: [
    node("__start__", "start"),
    node("a"),
    node("b", "tool", { error_count: 1 }),
    node("d"),
  ],
  edges: [
    edge("__start__", "a"),
    edge("a", "b", { transition_count: 3 }),
    edge("b", "d"),
  ],
};

describe("Error Feed diff graph through buildFlowData (unchanged)", () => {
  const { failAnnotated } = buildGraphDiff(fail, pass);
  const flow = buildFlowData(failAnnotated, "TB");

  it("keeps the regular edge styling", () => {
    const e = flow.edges.find((x) => x.source === "a" && x.target === "b");
    expect(e).toEqual({
      id: "e-1",
      source: "a",
      target: "b",
      type: "default",
      animated: false,
      markerEnd: { type: "arrowclosed", color: "#94a3b8", width: 8, height: 8 },
      style: { stroke: "#cbd5e1", strokeWidth: 2 },
      label: "×3",
      labelStyle: { fontSize: 9, fill: "#94a3b8", fontWeight: 500 },
      labelBgStyle: { fill: "#ffffff", fillOpacity: 0.9 },
      labelBgPadding: [3, 2],
    });
    const plain = flow.edges.find((x) => x.source === "b" && x.target === "d");
    expect(plain).toEqual({
      id: "e-2",
      source: "b",
      target: "d",
      type: "default",
      animated: false,
      markerEnd: { type: "arrowclosed", color: "#94a3b8", width: 8, height: 8 },
      style: { stroke: "#cbd5e1", strokeWidth: 1.2 },
    });
  });

  it("keeps the SKIPPED PATH edge for a ghosted step", () => {
    const e = flow.edges.find((x) => x.target === "ghost-c");
    expect(e).toEqual({
      id: "e-skipped-3",
      source: "a",
      target: "ghost-c",
      type: "default",
      animated: false,
      markerEnd: {
        type: "arrowclosed",
        color: errorPalette.main,
        width: 10,
        height: 10,
      },
      style: {
        stroke: errorPalette.main,
        strokeWidth: 1.6,
        strokeDasharray: "6 4",
      },
      label: "SKIPPED PATH",
      labelStyle: {
        fontSize: 9,
        fill: "#fff",
        fontWeight: 700,
        letterSpacing: "0.04em",
      },
      labelBgStyle: { fill: errorPalette.main, fillOpacity: 0.95 },
      labelBgPadding: [5, 3],
      labelBgBorderRadius: 4,
      zIndex: 5,
    });
  });

  it("passes the diff fields through to node data untouched", () => {
    const data = (id) => flow.nodes.find((n) => n.id === id).data;
    expect(data("b")).toMatchObject({
      _diffStatus: "matched-regressed",
      _isFailurePoint: true,
    });
    expect(data("d")).toMatchObject({
      _diffStatus: "fail-only",
      _isFailurePoint: false,
    });
    expect(data("ghost-c")).toMatchObject({
      _diffStatus: "pass-only-ghost",
      _originalId: "c",
    });
    expect(data("a")).not.toHaveProperty("_diffColor");
    expect(data("a")).not.toHaveProperty("_visits");
  });
});

// ---------------------------------------------------------------------------
// R4 lane T: two-run statuses (added after the pins above were green).
describe("getDiffOverlay: existing statuses unchanged", () => {
  it.each([
    [
      "fail-only",
      false,
      { ringColor: errorPalette.main, badge: "+", label: null },
    ],
    [
      "pass-only",
      false,
      { ringColor: expect.any(String), badge: "+", label: null },
    ],
    [
      "pass-only-ghost",
      false,
      { ringColor: expect.any(String), badge: "−", label: "Missing" },
    ],
    [
      "matched-regressed",
      false,
      { ringColor: "#F5A623", badge: "Δ", label: null },
    ],
    ["matched", false, { ringColor: null, badge: null, label: null }],
    [undefined, false, { ringColor: null, badge: null, label: null }],
    [
      "matched",
      true,
      { ringColor: errorPalette.main, badge: "✕", label: "Failed" },
    ],
  ])("%s (failure %s)", (status, failed, expected) => {
    const out = getDiffOverlay(status, failed);
    expect(out).toEqual(expected);
    expect(out).not.toHaveProperty("visits");
  });
});

describe("getDiffOverlay: two-run statuses", () => {
  const data = { _diffColor: "#7c3aed", _visits: { a: 2, b: 1 } };
  it("rings A-only and B-only nodes in the run colour with a visit chip", () => {
    expect(getDiffOverlay("only-a", false, data)).toEqual({
      ringColor: "#7c3aed",
      badge: "A",
      label: null,
      visits: "2× · 1×",
    });
    expect(getDiffOverlay("only-b", false, data).badge).toBe("B");
  });
  it("keeps nodes in both runs neutral, regressed ones amber Δ", () => {
    expect(getDiffOverlay("both", false, data)).toMatchObject({
      ringColor: null,
      badge: null,
      visits: "2× · 1×",
    });
    expect(getDiffOverlay("both-regressed", false, data)).toMatchObject({
      ringColor: "#F5A623",
      badge: "Δ",
    });
  });
  it("lets `Failed here` beat the run colour", () => {
    expect(getDiffOverlay("only-b", true, data)).toMatchObject({
      ringColor: errorPalette.main,
      badge: "✕",
      label: "Failed",
    });
  });
});

describe("buildFlowData: two-run edges", () => {
  it("colours run-only edges and dashes A-only paths; plain edges unchanged", () => {
    const g = {
      nodes: [node("a"), node("b"), node("c")],
      edges: [
        edge("a", "b", { _diffStatus: "only-a", _diffColor: "#16a34a" }),
        edge("a", "c", {
          _diffStatus: "only-b",
          _diffColor: "#7c3aed",
          transition_count: 2,
        }),
        edge("b", "c", { _diffStatus: "both" }),
        edge("c", "c", { _label: "18 · all fail" }),
      ],
    };
    const flow = buildFlowData(g, "TB");
    const by = (s, t) =>
      flow.edges.find((e) => e.source === s && e.target === t);
    expect(by("a", "b").style).toMatchObject({
      stroke: "#16a34a",
      strokeDasharray: "6 4",
    });
    expect(by("a", "c").style.stroke).toBe("#7c3aed");
    expect(by("a", "c").label).toBe("×2");
    expect(by("b", "c").style.stroke).toBe("#cbd5e1");
    expect(by("c", "c").label).toBe("18 · all fail");
  });
});

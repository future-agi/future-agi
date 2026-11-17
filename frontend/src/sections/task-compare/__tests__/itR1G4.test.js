// IT-R1 G4 · one trace predicate, voice Why, ranked call difference, step-
// flow graphs, normalised MCP tool names and the rail's Differ definition.
import { describe, expect, it } from "vitest";
import { trialHasTrace } from "src/sections/common/Trace/traceChipModel";
import { toolDisplayParts } from "src/sections/common/Align/normalize";
import {
  rankedCallDifference,
  toolActionPhrase,
} from "src/sections/common/Lenses/lensModel";
import {
  buildFlowGraph,
  flowNameOf,
  graphOfTree,
  isStepFlow,
  withStepFlow,
} from "src/sections/common/Trajectory/flowGraph";
import { voiceWhy } from "../whyModel";
import { runsDiffer } from "../workspaceModel";

const span = (id, name, type, t, extra = {}) => ({
  observation_span: {
    id,
    name,
    observation_type: type,
    start_time: new Date(Date.UTC(2026, 9, 17, 10, 0, t)).toISOString(),
    latency_ms: 100,
    ...extra,
  },
  children: [],
});
const tree = (rootType, children, attrs = {}) => [
  {
    observation_span: {
      id: "root",
      name: "root",
      observation_type: rootType,
      start_time: new Date(Date.UTC(2026, 9, 17, 10, 0, 0)).toISOString(),
      span_attributes: attrs,
    },
    children,
  },
];

describe("trialHasTrace (one predicate for Why, Steps, Graph, Timeline)", () => {
  it("steps, a trace id, a call or a harness transcript count", () => {
    expect(trialHasTrace({ steps: [{ n: 1 }] })).toBe(true);
    expect(trialHasTrace({ steps: [], trace_id: "t1" })).toBe(true);
    expect(trialHasTrace({ steps: [], output_kind: "call" })).toBe(true);
    expect(
      trialHasTrace({ steps: [], workspace: { transcript: [{ at_ms: 1 }] } }),
    ).toBe(true);
    expect(
      trialHasTrace({
        steps: [],
        output_kind: "image",
        output: { kind: "image", url: "data:x" },
      }),
    ).toBe(false);
    expect(trialHasTrace(null)).toBe(false);
  });
});

describe("toolDisplayParts", () => {
  it("MCP tools read by their bare name with the server after", () => {
    expect(toolDisplayParts("mcp__acme-support__lookup_customer")).toEqual({
      name: "lookup_customer",
      server: "acme-support",
      label: "lookup_customer · MCP acme-support",
    });
    expect(toolDisplayParts("lookup_order").label).toBe("lookup_order");
    expect(flowNameOf("caller (simulated)")).toBe("caller");
  });
});

const call = (turns, metrics = {}) => ({ kind: "call", turns, metrics });
const T = (i, role, text, extra = {}) => ({ i, role, text, ...extra });

describe("rankedCallDifference + voiceWhy", () => {
  const a = call(
    [
      T(1, "agent", "Hi"),
      T(2, "caller", "My blender cracked"),
      T(3, "agent", "Sorry, order number?", { interrupted_by: "caller" }),
      T(4, "caller", "A-8830"),
      T(5, "agent", "Replacement on its way", {
        tool: { name: "offer_replacement", args: { order_id: "A-8830" } },
      }),
    ],
    {
      latency_ms: { p50: 1400 },
      interruptions: { agent_kept_talking: 2 },
    },
  );
  const b = call(
    [
      T(1, "agent", "Hello"),
      T(2, "caller", "My blender cracked"),
      T(3, "agent", "Sorry to hear that, the order number please?"),
      T(4, "caller", "A-8830"),
      T(5, "agent", "Refund issued", {
        tool: { name: "issue_refund", args: { order_id: "A-8830" } },
      }),
    ],
    { latency_ms: { p50: 900 }, interruptions: { agent_kept_talking: 0 } },
  );

  it("a different tool outranks an earlier interruption or wording change", () => {
    const d = rankedCallDifference([a, b], { names: ["v1", "v2"] });
    expect(d.kind).toBe("tool");
    expect(d.turn).toBe(5);
    expect(d.short).toBe(
      "v1 offered a replacement (offer_replacement), v2 issued a refund (issue_refund)",
    );
    expect(toolActionPhrase("lookup_order")).toBe("looked up an order");
    expect(toolActionPhrase("frobnicate")).toBe("called frobnicate");
  });

  it("the Why line reads the calls' own metrics", () => {
    const lanes = [
      { id: "a", label: "v1", verdict: { pass: 0, scored: 1 } },
      { id: "b", label: "v2", verdict: { pass: 1, scored: 1 } },
    ];
    const text = voiceWhy({
      lanes,
      calls: [a, b],
      names: new Map([
        ["a", "v1"],
        ["b", "v2"],
      ]),
      fmtMs: (ms) => `${(ms / 1000).toFixed(1)} s`,
    });
    expect(text).toBe(
      "V2 finished the task, v1 did not · v2 never talked over the caller (v1 did 2×) · v2 replied in 0.9 s vs 1.4 s (p50) · first difference: turn 5 · v1 offered a replacement (offer_replacement), v2 issued a refund (issue_refund).",
    );
    expect(voiceWhy({ lanes, calls: [a, null] })).toBeNull();
  });
});

describe("step-flow graphs (harness transcripts, calls)", () => {
  const harness = tree(
    "agent",
    [
      span("s1", "customer (simulated)", "chain", 1),
      span("s2", "assistant", "llm", 2),
      span("s3", "mcp__acme-support__lookup_order", "tool", 3),
      span("s4", "mcp__acme-support__lookup_order", "tool", 4),
      span("s5", "assistant", "llm", 5),
      span("s6", "End state", "chain", 6),
    ],
    { "fi.harness": "Claude Code" },
  );

  it("marks harness and voice trees; leaves the rest untouched", () => {
    const flow = withStepFlow(harness, { kind: "harness" });
    expect(isStepFlow(flow)).toBe(true);
    expect(Object.keys(flow)).toEqual(["0"]);
    const plain = tree("agent", [span("x", "plan", "llm", 1)]);
    expect(withStepFlow(plain, { kind: "agent" })).toBe(plain);
    const voice = tree("conversation", [span("v1", "agent", "llm", 1)]);
    expect(isStepFlow(withStepFlow(voice, { kind: "voice" }))).toBe(true);
  });

  it("follows the steps in order with bare tool names, Start and End", () => {
    const g = buildFlowGraph(withStepFlow(harness, { kind: "harness" }));
    expect(g.nodes.map((n) => n.id)).toEqual([
      "__start__",
      "customer",
      "assistant",
      "lookup_order",
      "__end__",
    ]);
    const lookup = g.nodes.find((n) => n.id === "lookup_order");
    expect(lookup.span_count).toBe(2);
    expect(lookup._subtitle).toBe("MCP acme-support");
    const edges = g.edges.map(
      (e) => `${e.source}>${e.target}:${e.transition_count}`,
    );
    expect(edges).toEqual([
      "__start__>customer:1",
      "customer>assistant:1",
      "assistant>lookup_order:1",
      "lookup_order>lookup_order:1",
      "lookup_order>assistant:1",
      "assistant>__end__:1",
    ]);
    expect(g.nodeToSpanIds.lookup_order).toEqual(["s3", "s4"]);
    expect(graphOfTree(harness).nodes.some((n) => n.id === "__start__")).toBe(
      true,
    );
  });
});

describe("rail Differ = any result or check difference", () => {
  const c = (trials, failed) => ({ trials, failed_evals: failed });
  it("2 of 3 vs 3 of 3 differs; same results and checks do not", () => {
    expect(
      runsDiffer({
        a: c([0, 1, 1], [["reply_rubric"], [], []]),
        b: c([1, 1, 1], [[], [], []]),
      }),
    ).toBe(true);
    expect(
      runsDiffer({
        a: c([0, 1, 1], [["x"], [], []]),
        b: c([1, 0, 1], [[], ["y"], []]),
      }),
    ).toBe(true);
    expect(runsDiffer({ a: c([1, 1], [[], []]), b: c([1, 1], [[], []]) })).toBe(
      false,
    );
    expect(runsDiffer({ a: c([1], [[]]), b: c([], []) })).toBe(false);
  });
});

describe("voiceChangeLine", () => {
  it("names the platform, model, STT and TTS changes", async () => {
    const { voiceChangeLine } = await import("../whyModel");
    const a = {
      voice: {
        provider: "websocket",
        llm: { model: "gpt-4o-mini" },
        stt: { model: "nova-2" },
        tts: { provider: "elevenlabs", voice: "Rachel" },
      },
    };
    const b = {
      voice: {
        provider: "livekit",
        llm: { model: "gpt-4.1-mini" },
        stt: { model: "nova-3" },
        tts: { provider: "cartesia", voice: "Sarah" },
      },
    };
    expect(voiceChangeLine(a, b)).toBe(
      "WebSocket → LiveKit · gpt-4o-mini → gpt-4.1-mini · STT nova-2 → nova-3 · TTS ElevenLabs Rachel → Cartesia Sarah",
    );
    expect(voiceChangeLine(a, a)).toBeNull();
    expect(voiceChangeLine(a, null)).toBeNull();
  });
});

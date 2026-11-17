import { describe, expect, it } from "vitest";
import { firstDifferenceText } from "src/sections/common/Align/milestones";
import {
  alignPairMilestones,
  explainDivergence,
  flattenTrace,
  gutterGlyph,
  normalizeSteps,
  rowOfFirstDifference,
  traceAgentOf,
} from "../alignSteps";
import traceA from "./fixtures/trace-a.json";
import traceB from "./fixtures/trace-b.json";
import traceD from "./fixtures/trace-d.json";
import trace1052 from "./fixtures/trace-1052.json";
import trace1044 from "./fixtures/trace-1044.json";

const steps = (t, opts) => normalizeSteps(flattenTrace(t), opts);

describe("alignStepsAcross (TS #2 T26, trial 1) on G0 milestones (R3)", () => {
  const a = steps(traceB);
  const b = steps(traceA);
  const labels = { labelA: "#2B", labelB: "#2A" };
  const { rows, markers } = alignPairMilestones(a, b, {
    ...labels,
    kindA: "agent",
    kindB: "prompt",
  });
  const div = { row: rowOfFirstDifference(rows, markers.firstDifference) };

  it("prints G0's first difference (R2 beat 4 line 1)", () => {
    expect(firstDifferenceText(markers.firstDifference)).toBe(
      'First difference: step 2 · lookup_order input changed (order_id "8830" → "A-8830")',
    );
  });

  it("never pairs an internal step with the other run's reply", () => {
    expect(
      rows.some(
        (r) => r.a?.name === "classify_intent" && r.b && r.kind !== "only_a",
      ),
    ).toBe(false);
    expect(rows.find((r) => r.a?.name === "classify_intent")?.sub).toBe(true);
  });

  it("explains the divergence on line 2 with the mode note", () => {
    const out = explainDivergence(div, {
      ...labels,
      stepsA: a,
      promptOf: (node) =>
        node === "classify_intent"
          ? "Extract the order number as digits only."
          : null,
    });
    expect(out.text).toBe(
      '#2B got 404 for "8830"; #2A got 200 for "A-8830". The input came from step 1: classify_intent returned "8830" (prompt: "Extract the order number as digits only.").',
    );
    expect(out.modeNote).toBe(
      "lookup_order is mocked in #2A, so this difference is the agent's, not the tool's.",
    );
  });

  it("marks gutter glyphs", () => {
    expect(rows.some((r) => gutterGlyph(r) === "~")).toBe(true);
    expect(rows.some((r) => gutterGlyph(r) === "−")).toBe(true);
  });
});

describe("harness sides", () => {
  it("collapses assistant turns, names customers and the end state", () => {
    const d = steps(traceD, { harness: true });
    expect(d[0]).toMatchObject({ nkind: "customer", name: "customer" });
    expect(d[1].name).toMatch(/^assistant · \d+ words$/);
    expect(d.find((s) => s.nkind === "tool")).toMatchObject({ mcp: true });
    expect(d.at(-1).name).toBe("End state: 3 changes");
  });
});

describe("Observe pair (chat-1052 vs chat-1044, turn 2)", () => {
  it("pins both banner lines", () => {
    const a = steps(trace1052);
    const b = steps(trace1044);
    const labels = { labelA: "chat-1052", labelB: "chat-1044" };
    const { rows, markers } = alignPairMilestones(a, b, labels);
    const div = { row: rowOfFirstDifference(rows, markers.firstDifference) };
    expect(firstDifferenceText(markers.firstDifference)).toBe(
      'First difference: step 2 · lookup_order input changed (order_id "8830" → "A-3301")',
    );
    expect(
      explainDivergence(div, {
        ...labels,
        stepsA: a,
        agentA: traceAgentOf(trace1052),
        agentB: traceAgentOf(trace1044),
      }).text,
    ).toBe(
      'chat-1052 got 404 for "8830"; chat-1044 got 200 for "A-3301". Both traces ran Support Agent v3.',
    );
  });
});

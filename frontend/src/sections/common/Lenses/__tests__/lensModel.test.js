import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { VOICE_CALLS } from "src/test-fixtures/voice-calls";
import {
  alignCalls,
  callCause,
  callWer,
  firstCallDifference,
  gapsOf,
  imageDiff,
  latencyRows,
  loudnessGain,
  matchLoudness,
  nearestRank,
  pairsOf,
  seekTimesFor,
  slowestTurn,
  stageSplit,
  stepsOf,
  wer,
  wordDiff,
} from "../lensModel";

const v1 = VOICE_CALLS.A; // S1 · Mike · Acme Support v1
const v2 = VOICE_CALLS.B; // S1 · Mike · Acme voice v2
const names = ["Acme Support v1", "Acme voice v2"];

describe("turn alignment", () => {
  it("pairs a call by caller turn, greeting first", () => {
    const p = pairsOf(v1);
    expect(p[0].caller).toBeNull();
    expect(p[0].agents.map((t) => t.i)).toEqual([0]);
    expect(p[2].caller.i).toBe(3);
    expect(p[2].agents.map((t) => t.i)).toEqual([4, 5]);
  });

  it("aligns calls with unequal turn counts by caller-turn index", () => {
    expect(v1.turns.length).not.toBe(v2.turns.length);
    const rows = alignCalls([v1, v2]);
    expect(rows.map((r) => r.k)).toEqual([0, 1, 2, 3, 4]);
    const third = rows.find((r) => r.k === 3);
    expect(third.lanes[0].caller.i).toBe(6);
    expect(third.lanes[1].caller.i).toBe(5);
    // a lane that ran out of pairs is null, never a crash
    const short = { ...v2, turns: v2.turns.slice(0, 4) };
    const rows2 = alignCalls([v1, short]);
    expect(rows2.at(-1).lanes[1]).toBeNull();
  });

  it("steps carry the baseline's turn numbers and seek each lane to its own turn", () => {
    const rows = alignCalls([v1, v2]);
    const steps = stepsOf(rows);
    const n = steps.map((s) => s.n);
    expect(n.slice(0, 6)).toEqual([0, 1, 2, 3, 4, 5]);
    const step3 = steps.find((s) => s.n === 3);
    const [a, b] = seekTimesFor(rows, step3, [v1, v2]);
    expect(a).toBeCloseTo(v1.turns[3].start_ms / 1000, 5);
    expect(b).toBeCloseTo(v2.turns[3].start_ms / 1000, 5);
  });
});

describe("firstCallDifference", () => {
  it("names the misheard words at turn 3 on S1 · Mike (tool args differ)", () => {
    const d = firstCallDifference([v1, v2], { names, callerName: "Mike" });
    expect(d.kind).toBe("tool_args");
    expect(d.turn).toBe(3);
    expect(d.text).toBe(
      'First difference: turn 3 · Mike said "A as in apple, eight eight three zero"; Acme voice v2 heard "eight eight three zero"',
    );
  });

  it("reports a tool-args change without a mishearing", () => {
    const heardRight = {
      ...v2,
      turns: v2.turns.map((t) =>
        t.role === "caller" ? { ...t, text: t.scripted_text } : t,
      ),
    };
    const d = firstCallDifference([v1, heardRight], { names });
    expect(d.kind).toBe("tool_args");
    expect(d.text).toBe(
      'First difference: turn 4 · Acme Support v1 called lookup_order({"order_id":"A-8830"}); Acme voice v2 called lookup_order({"order_id":"8830"})',
    );
  });

  it("reports a reply that changed by more than 30% of its words", () => {
    const changed = {
      ...v1,
      turns: v1.turns.map((t) =>
        t.i === 2
          ? { ...t, text: "Right, what went wrong with the order?" }
          : t,
      ),
    };
    const d = firstCallDifference([v1, changed], { names });
    expect(d.kind).toBe("reply");
    expect(d.turn).toBe(2);
    // a small rewording (under 30%) is not a difference
    const tweak = {
      ...v1,
      turns: v1.turns.map((t) =>
        t.i === 2 ? { ...t, text: t.text.replace("please", "now") } : t,
      ),
    };
    expect(firstCallDifference([v1, tweak], { names })).toBeNull();
  });

  it("reports an early end", () => {
    const ended = { ...v1, turns: v1.turns.slice(0, 6) };
    const d = firstCallDifference([v1, ended], { names });
    expect(d.kind).toBe("ended");
    expect(d.turn).toBe(6);
  });

  it("returns null for identical calls and never compares greetings", () => {
    expect(firstCallDifference([v1, v1], { names })).toBeNull();
    const g = {
      ...v1,
      turns: v1.turns.map((t) =>
        t.i === 0 ? { ...t, text: "Hello there, Acme here." } : t,
      ),
    };
    expect(firstCallDifference([v1, g], { names })).toBeNull();
  });

  it("puts no digit literal in its sentence templates (§C10)", () => {
    const src = fs.readFileSync(
      path.resolve(process.cwd(), "src/sections/common/Lenses/lensModel.js"),
      "utf8",
    );
    const templates = src.match(/`[^`]*First difference[^`]*`/g) ?? [];
    expect(templates.length).toBeGreaterThan(0);
    templates.forEach((t) =>
      expect(t.replace(/\$\{[^}]*\}/g, "")).not.toMatch(/\d/),
    );
  });

  it("follows the data: moving the mishearing moves the sentence", () => {
    const later = {
      ...v2,
      turns: v2.turns.map((t) =>
        t.i === 3 ? { ...t, text: "It's eight three zero." } : t,
      ),
    };
    const d = firstCallDifference([v1, later], { names, callerName: "Mike" });
    expect(d.heard).toBe("eight three zero");
  });
});

describe("WER", () => {
  it("counts word edits against the scripted text", () => {
    expect(
      wer("It's A as in apple, eight eight three zero.", "It's A, 8830."),
    ).toEqual({
      edits: 3,
      words: 9,
      wer: 3 / 9,
    });
    expect(wer("", "anything").wer).toBeNull();
    expect(wer("Thank you.", "thank you").edits).toBe(0);
  });

  it("matches the world's per-call WER", () => {
    expect(callWer(v1).wer).toBeCloseTo(v1.metrics.wer, 3);
    expect(callWer(v2).wer).toBeCloseTo(v2.metrics.wer, 3);
  });

  it("word diff marks missing and extra words", () => {
    const d = wordDiff("It's A as in apple, eight", "It's eight");
    expect(d.filter((x) => x.op === "missing").map((x) => x.text)).toEqual([
      "A",
      "as",
      "in",
      "apple,",
    ]);
    const n = wordDiff(
      "It's A as in apple, eight eight three zero.",
      "It's A, 8830.",
    );
    expect(n.filter((x) => x.op === "missing").map((x) => x.text)).toEqual([
      "as",
      "in",
      "apple,",
    ]);
    expect(n.find((x) => x.text === "8830.").op).toBe("same");
    const e = wordDiff("my blender", "might blender");
    expect(e.map((x) => x.op)).toEqual(["missing", "extra", "same"]);
  });
});

describe("latency", () => {
  it("stage splits add up to the response gap", () => {
    v1.turns
      .filter((t) => t.gap_ms != null)
      .forEach((t) => expect(stageSplit(t).ok).toBe(true));
    expect(stageSplit({ gap_ms: 900 }).ok).toBe(false);
  });

  it("nearest-rank percentiles match the call's own metrics", () => {
    [v1, v2].forEach((c) => {
      expect(nearestRank(gapsOf(c), 0.5)).toBe(c.metrics.latency_ms.p50);
      expect(nearestRank(gapsOf(c), 0.9)).toBe(c.metrics.latency_ms.p90);
    });
    expect(nearestRank([5, 1, 3, 2, 4], 0.5)).toBe(3);
    expect(nearestRank([5, 1, 3, 2, 4], 0.9)).toBe(5);
    expect(nearestRank([], 0.5)).toBeNull();
  });

  it("latency rows align agent answers; turn 4 is v1 1.6 s vs v2 0.7 s", () => {
    const rows = latencyRows([v1, v2]);
    const t4 = rows.find((r) => r.n === 4);
    expect(t4.lanes.map((l) => l.gap)).toEqual([1600, 700]);
    expect(t4.lanes[0].interrupted).toEqual({ stopMs: 1300, turn: 5 });
    const slow = slowestTurn(rows);
    expect(slow.gaps[slow.laneIndex]).toBe(
      Math.max(...gapsOf(v1), ...gapsOf(v2)),
    );
  });

  it("text-only lanes give no latency", () => {
    const text = VOICE_CALLS.C;
    const rows = latencyRows([v1, text]);
    expect(rows.every((r) => r.lanes[1] === null)).toBe(true);
  });
});

describe("loudness and images", () => {
  it("loudness gain from peaks RMS", () => {
    const g = loudnessGain([0.5, 0.5], [0.25, 0.25]);
    expect(g.db).toBeCloseTo(-6.02, 2);
    expect(g.linear).toBeCloseTo(0.5, 5);
    expect(loudnessGain([0, 0], [0.5])).toBeNull();
    const m = matchLoudness([[0.4], [0.2], null]);
    expect(m[0].linear).toBeCloseTo(0.5, 5);
    expect(m[1].linear).toBe(1);
    expect(m[2]).toBeNull();
  });

  it("image difference counts changed pixels", () => {
    const a = new Uint8ClampedArray([0, 0, 0, 255, 10, 10, 10, 255]);
    const b = new Uint8ClampedArray([0, 0, 0, 255, 200, 10, 10, 255]);
    const d = imageDiff(a, b, { gain: 2 });
    expect(d.changed).toBe(1);
    expect(d.share).toBe(0.5);
    expect(d.pixels[4]).toBe(255);
  });
});

describe("IT-R5 G4 P5: the call's cause line", () => {
  it("names what the lane heard and what the tool returned", () => {
    expect(callCause([v1, v2], { names })?.text).toBe(
      'Acme voice v2 heard "8830" for "A-8830", so lookup_order returned 404',
    );
  });
  it("is null when no lane's tool call failed", () => {
    expect(callCause([v1, v1], { names })).toBe(null);
  });
});

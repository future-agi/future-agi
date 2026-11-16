import { describe, expect, it } from "vitest";
import { sequenceOf } from "../participantsSequence";
import { participantsFromTimeline } from "../workerTimeline";
import { CONTRACT_SAMPLES } from "../workerTimelineSamples";
import { USER_NODE } from "../participantsGraph";

const rec = (name) => participantsFromTimeline(CONTRACT_SAMPLES[name]);

describe("sequenceOf (Participants design A)", () => {
  Object.keys(CONTRACT_SAMPLES).forEach((name) => {
    it(`${name}: numbers every step once, in start order`, () => {
      const seq = sequenceOf(rec(name), { callerName: "Jamal Carter" });
      expect(seq.steps.map((s) => s.n)).toEqual(seq.steps.map((_, i) => i + 1));
      const timed = seq.steps.filter((s) => s.at != null).map((s) => s.at);
      expect(timed).toEqual([...timed].sort((a, b) => a - b));
      expect(seq.lanes[0]).toMatchObject({
        id: USER_NODE,
        name: "Jamal Carter",
      });
      // every step and answer has words
      seq.steps.forEach((s) => {
        expect(s.label).toBeTruthy();
        if (s.answer) expect(s.answer.label).toBeTruthy();
      });
    });

    it(`${name}: an answer never comes back before the work it waited on`, () => {
      const seq = sequenceOf(rec(name));
      const pos = (type, n) =>
        seq.rows.findIndex((r) => r.n === n && r.type === type);
      seq.steps
        .filter((s) => s.kind === "handoff" && s.answer)
        .forEach((s) => {
          const back = seq.rows.findIndex(
            (r) => r.n === s.n && r.type !== "go",
          );
          expect(back).toBeGreaterThan(pos("go", s.n));
          seq.steps
            .filter((k) => k.n > s.n && k.from === s.to)
            .forEach((k) => {
              const kidRow = pos(k.kind === "tool" ? "tool" : "go", k.n);
              expect(back).toBeGreaterThan(kidRow);
            });
        });
    });
  });

  it("tool calls are steps of their own on the lane that ran them", () => {
    const seq = sequenceOf(rec("returns"));
    const tools = seq.steps.filter((s) => s.kind === "tool");
    expect(tools.length).toBeGreaterThan(0);
    tools.forEach((t) => {
      expect(seq.lanes.some((l) => l.id === t.from)).toBe(true);
      expect(t.result).toBeTruthy();
    });
  });

  it("parallel hand-offs share a same-time bracket", () => {
    const seq = sequenceOf(rec("parallel"));
    expect(seq.groups.length).toBeGreaterThan(0);
  });

  it("a hand-off that never answered is a problem named after its step", () => {
    const sample = Object.keys(CONTRACT_SAMPLES).find((k) => {
      const s = sequenceOf(rec(k));
      return s.steps.some((x) => x.answer?.kind === "none" && x.answer.bad);
    });
    if (!sample) return;
    const seq = sequenceOf(rec(sample));
    const bad = seq.steps.find(
      (x) => x.answer?.kind === "none" && x.answer.bad,
    );
    expect(seq.problems.map((p) => p.n)).toContain(bad.n);
    expect(seq.problems.find((p) => p.n === bad.n).text).toMatch(
      new RegExp(`step ${bad.n}$`),
    );
  });

  it("a worker that took over the call hands nothing back", () => {
    const seq = sequenceOf(rec("transfer"));
    expect(seq.rows.some((r) => r.type === "took")).toBe(true);
  });

  it("back-to-back turns the coordinator answered alone fold into one step", () => {
    const p = {
      coordinator: { id: "desk", name: "Front desk" },
      observed: true,
      workers: [],
      turns: [1, 2, 3].map((n) => ({
        n,
        request: `Ask ${n}.`,
        reply: `Reply ${n}.`,
        at: n * 10,
        replyAt: n * 10 + 2,
        workers: [],
      })),
    };
    const seq = sequenceOf(p);
    expect(seq.steps).toHaveLength(1);
    expect(seq.steps[0].turns).toBe(3);
  });
});

describe("sequenceOf · attribution (plan MA-2)", () => {
  it("a tool call tied to no worker goes on an Unknown lane, not the coordinator's", () => {
    const seq = sequenceOf(rec("unattributed"));
    const untied = seq.steps.filter((s) => s.untied);
    expect(untied.length).toBeGreaterThan(0);
    untied.forEach((s) => {
      expect(s.from).not.toBe(seq.lanes[1].id);
      const lane = seq.lanes.find((l) => l.id === s.from);
      expect(lane.role).toBe("Unknown");
    });
  });
});

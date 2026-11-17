import { describe, expect, it } from "vitest";
import { sequenceOf } from "../participantsSequence";
import { participantsFromTimeline } from "../workerTimeline";
import { CONTRACT_SAMPLES } from "../workerTimelineSamples";
import { USER_NODE } from "../participantsGraph";
import { LONG_CALL } from "../participantsLong";

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

describe("long calls: turns and loops (Chintan, 17 Nov)", () => {
  const seq = sequenceOf(LONG_CALL, { callerName: "Jamal Carter" });

  it("keeps every step's caller turn", () => {
    expect(seq.steps).toHaveLength(27);
    expect(seq.turnGroups.map((g) => g.steps.length)).toEqual([5, 7, 9, 5, 1]);
    expect(seq.turnGroups[1].bad).toBe(true);
  });

  it("finds a retried tool and a repeated round as loops", () => {
    const [retry, rounds] = seq.loops;
    expect(retry).toMatchObject({
      first: 8,
      last: 12,
      per: 1,
      times: 5,
      badCount: 5,
    });
    expect(rounds).toMatchObject({
      first: 14,
      last: 21,
      per: 2,
      times: 4,
      badCount: 0,
    });
    expect(rounds.with).toEqual(["check_queue"]);
  });

  it("says a problem repeated in a loop once, with its steps", () => {
    const loopProblem = seq.problems.find((p) => p.loop);
    expect(loopProblem.text).toBe("issue_refund failed 5 times");
    expect(loopProblem.range).toEqual([8, 12]);
    expect(seq.problems).toHaveLength(2);
  });

  /* Karthik, 17 Nov: refused by the world, with its reason, apart from failed. */
  it("keeps a call the world refused apart from failures, with its reason", () => {
    const step = seq.steps.find((s) => s.n === 5);
    expect(step).toMatchObject({
      tool: "issue_refund",
      refused: true,
      bad: false,
      reason: "Refunds over $200 need a supervisor's sign-off",
      result: "Refused · Refunds over $200 need a supervisor's sign-off",
    });
    expect(seq.refusals).toEqual([
      expect.objectContaining({ n: 5, tool: "issue_refund", times: 1 }),
    ]);
    expect(seq.problems.map((x) => x.n)).not.toContain(5);
    expect(seq.turnGroups[0]).toMatchObject({ bad: false, refused: 1 });
  });

  it("counts a refusal inside a loop apart from the failures", () => {
    const p = structuredClone(LONG_CALL);
    Object.assign(p.turns[1].workers[0].calls[0], {
      failed: false,
      blocked: true,
    });
    const s2 = sequenceOf(p);
    expect(s2.problems.filter((x) => x.loop).map((x) => x.text)).toEqual([
      "issue_refund failed 4 times",
    ]);
    expect(s2.refusals.map((r) => [r.n, r.reason])).toEqual([
      [5, "Refunds over $200 need a supervisor's sign-off"],
      [8, "Not one of this worker's tools"],
    ]);
    expect(s2.loops[0].outcomes).toEqual({
      failed: 4,
      refused: 1,
      noAnswer: 0,
    });
  });

  it("says a no-answer inside a loop as its own problem, not a failure", () => {
    const p = structuredClone(LONG_CALL);
    Object.assign(p.turns[2].workers[3], { state: "timeout", endedAt: null });
    const seq = sequenceOf(p);
    expect(seq.problems.map((x) => x.text)).toContain(
      "Escalations didn't answer step 20",
    );
    expect(seq.loops[1].outcomes).toEqual({
      failed: 0,
      refused: 0,
      noAnswer: 1,
    });
  });

  it("names merged turns by the record's own numbers", () => {
    const p = structuredClone(LONG_CALL);
    p.turns.push(
      {
        n: 6,
        at: 92,
        request: "One more thing.",
        reply: "Sure.",
        replyAt: 94,
        workers: [],
        own: [],
      },
      {
        n: 7,
        at: 96,
        request: "Bye.",
        reply: "Bye!",
        replyAt: 97,
        workers: [],
        own: [],
      },
    );
    const last = sequenceOf(p).steps.at(-1);
    expect(last.turns).toBe(3);
    expect(last.full).toMatch(
      /^Turn 5: Okay, thanks\.\nTurn 6: One more thing\.\nTurn 7: Bye\.$/,
    );
  });

  /* Karthik, 17 Nov: we have the exact text, not a summary. */
  it("puts the exact recorded words on every arrow, never a made-up label", () => {
    const flat = (t) =>
      String(t ?? "")
        .replace(/\s*\n+\s*/g, " · ")
        .trim();
    [LONG_CALL, ...Object.keys(CONTRACT_SAMPLES).map(rec)].forEach((p) => {
      const seq = sequenceOf(p);
      seq.steps.forEach((s) => {
        if (s.kind !== "tool" && s.full) expect(s.label).toBe(flat(s.full));
        if (
          s.kind === "tool" &&
          s.output != null &&
          !s.blocked &&
          typeof s.output === "string"
        )
          expect(s.result).toBe(s.output);
        if (s.answer?.kind === "answer" && s.answer.full && !s.turns)
          expect(s.answer.label).toBe(flat(s.answer.full));
      });
    });
  });

  it("finds no loop in a short call", () => {
    Object.keys(CONTRACT_SAMPLES).forEach((name) => {
      expect(sequenceOf(rec(name)).loops).toEqual([]);
    });
  });
});

import { describe, expect, it } from "vitest";
import {
  alignTrials,
  routesOf,
  varyParticipantsForTrial,
} from "../participantsTrials";
import { sequenceOf } from "../participantsSequence";

const base = () => {
  const a = {
    id: "a",
    name: "Refunds",
    state: "returned",
    delegatedAt: 2,
    endedAt: 5,
    calls: [],
  };
  const b = {
    id: "b",
    name: "Shipping",
    state: "timeout",
    delegatedAt: 2,
    endedAt: 40,
    calls: [],
  };
  return {
    observed: true,
    summary: "Desk passed this call to 2 workers. 1 finished, 1 didn't answer.",
    coordinator: { id: "desk", name: "Desk" },
    workers: [a, b],
    turns: [
      {
        n: 1,
        request: "Refund me",
        reply: "Done",
        at: 0,
        replyAt: 45,
        workers: [a, b],
      },
    ],
  };
};

describe("trials (proposal 8)", () => {
  it("trial 1 is never changed", () => {
    const p = base();
    expect(
      varyParticipantsForTrial(p, { trialIndex: 0, status: "failed" }),
    ).toBe(p);
  });

  it("a later failing trial gets the timed-out worker's answer every other trial", () => {
    const q = varyParticipantsForTrial(base(), {
      trialIndex: 1,
      trialId: "x-2",
      status: "failed",
    });
    expect(q.workers.find((w) => w.id === "b").state).toBe("returned");
    const r = varyParticipantsForTrial(base(), {
      trialIndex: 2,
      trialId: "x-3",
      status: "failed",
    });
    expect(r.workers.find((w) => w.id === "b").state).toBe("timeout");
  });

  it("routes mark the first column where trials differ", () => {
    const s1 = sequenceOf(base());
    const s2 = sequenceOf(
      varyParticipantsForTrial(base(), {
        trialIndex: 1,
        trialId: "x-2",
        status: "failed",
      }),
    );
    const routes = routesOf([
      { idx: 0, status: "failed", seq: s1, placed: [] },
      { idx: 1, status: "failed", seq: s2, placed: [] },
    ]);
    expect(routes.split.label).toMatch(/Desk → Shipping/);
    expect(routes.split.text).toBe("trial 1: no answer · trial 2: answered");
  });

  it("identical trials have no split", () => {
    const s = sequenceOf(base());
    const routes = routesOf([
      { idx: 0, status: "failed", seq: s, placed: [] },
      { idx: 1, status: "failed", seq: s, placed: [] },
    ]);
    expect(routes.split).toBeNull();
  });

  it("aligned rows match by who asked whom; differing outcomes are not 'same'", () => {
    const s1 = sequenceOf(base());
    const s2 = sequenceOf(
      varyParticipantsForTrial(base(), {
        trialIndex: 1,
        trialId: "x-2",
        status: "failed",
      }),
    );
    const rows = alignTrials(s1, s2);
    const go = rows.filter((r) => r.key.startsWith("go:"));
    expect(go.every((r) => r.a && r.b)).toBe(true);
    const back = rows.filter((r) => r.key === "back:b>desk");
    expect(back.length).toBeGreaterThan(0);
    expect(back.some((r) => !r.same)).toBe(true);
  });
});

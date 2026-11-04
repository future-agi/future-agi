import { describe, it, expect } from "vitest";
import { arrange, asShown, isLong, seconds } from "../callWorkersUtils";

const at = (second) => `2026-10-31T12:00:${String(second).padStart(2, "0")}`;

describe("seconds", () => {
  it("reads a stored time with no zone as UTC, and anything else as unknown", () => {
    expect(seconds("2026-10-31T12:00:05")).toBe(
      Date.UTC(2026, 9, 31, 12, 0, 5) / 1000,
    );
    expect(seconds("2026-10-31T12:00:05Z")).toBe(
      seconds("2026-10-31T12:00:05"),
    );
    expect(seconds(null)).toBeNull();
    expect(seconds("not a time")).toBeNull();
  });
});

describe("asShown", () => {
  it("shows a JSON answer as indented code, a printed dict as it came, and text untouched", () => {
    expect(asShown('{"refund": "RF-7"}')).toBe(
      '```\n{\n  "refund": "RF-7"\n}\n```',
    );
    expect(asShown("{'refund': 'RF-7'}")).toBe("```\n{'refund': 'RF-7'}\n```");
    expect(asShown("Refund RF-7 issued")).toBe("Refund RF-7 issued");
  });
});

describe("isLong", () => {
  it("folds an answer past six lines or 480 characters", () => {
    expect(isLong("one\ntwo\nthree\nfour\nfive\nsix")).toBe(false);
    expect(isLong("one\ntwo\nthree\nfour\nfive\nsix\nseven")).toBe(true);
    expect(isLong("x".repeat(480))).toBe(false);
    expect(isLong("x".repeat(481))).toBe(true);
  });
});

describe("arrange", () => {
  it("nests a hand-off under the agent that made it, with its calls and its return", () => {
    const tree = arrange([
      { seq: 1, type: "delegated", worker_id: "lead", at: at(1) },
      { seq: 2, type: "delegated", worker_id: "helper", by: "lead", at: at(2) },
      { seq: 3, type: "tool_call", worker_id: "helper", tool: "t", at: at(3) },
      { seq: 4, type: "returned", worker_id: "helper", at: at(4) },
      { seq: 5, type: "returned", worker_id: "lead", at: at(5) },
      { seq: 6, type: "tool_call", worker_id: null, tool: "reply", at: at(6) },
    ]);

    const [lead, reply] = tree.items;
    expect(lead).toMatchObject({ worker_id: "lead", depth: 1 });
    expect(lead.returned.seq).toBe(5);
    const [helper] = lead.items;
    expect(helper).toMatchObject({ worker_id: "helper", depth: 2 });
    expect(helper.items.map((item) => item.event.tool)).toEqual(["t"]);
    expect(reply).toMatchObject({ kind: "call", key: 6 });
    expect(tree).toMatchObject({
      depth: 2,
      parallel: 0,
      depthOf: { lead: 1, helper: 2 },
    });
  });

  it("marks overlapping siblings as parallel and keeps a return with no hand-off at the top", () => {
    const tree = arrange([
      { seq: 1, type: "delegated", worker_id: "a", at: at(1) },
      { seq: 2, type: "transferred", worker_id: "b", at: at(2) },
      { seq: 3, type: "returned", worker_id: "a", at: at(4) },
      { seq: 4, type: "returned", worker_id: "b", at: at(5) },
      { seq: 5, type: "returned", worker_id: "c", at: at(6) },
    ]);

    expect(tree.parallel).toBe(2);
    expect(tree.items.map((item) => [item.worker_id, !!item.parallel])).toEqual(
      [
        ["a", true],
        ["b", true],
        ["c", false],
      ],
    );
    expect(tree.items[1].transfer).toBe(true);
    expect(tree.items[2].returned.seq).toBe(5);
  });
});

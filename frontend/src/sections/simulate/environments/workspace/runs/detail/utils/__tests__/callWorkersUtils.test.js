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
    expect(seconds("2026-10-31T17:30:05+05:30")).toBe(
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

  it.each([
    ["[1, 2, 3]"],
    ["['hotels', 'flights']"],
    ["[{'id': 1}]"],
    ["{'refund': 'RF-7', 'note': 'cut off…"],
  ])("fences %s as data", (data) => {
    expect(asShown(data)).toMatch(/^```\n/);
  });

  it.each([
    ["[Note] refund issued"],
    ["[Order 1042](https://x) shipped"],
    ["[1] the first source"],
    ["{Note} refund issued"],
  ])("leaves the prose %s as text", (prose) => {
    expect(asShown(prose)).toBe(prose);
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

  describe("two hand-offs to one worker open at once", () => {
    const hotels = {
      seq: 1,
      type: "delegated",
      worker_id: "searcher",
      task: "hotels",
      delegation_id: "A",
      at: at(1),
    };
    const flights = { ...hotels, seq: 2, task: "flights", delegation_id: "B" };
    const answer = (seq, id, result) => ({
      seq,
      type: "returned",
      worker_id: "searcher",
      delegation_id: id,
      result,
      at: at(seq + 2),
    });
    const answers = (tree) =>
      tree.items.map((one) => [one.delegated?.task, one.returned?.result]);

    it("gives each its own return, whichever comes back first", () => {
      expect(
        answers(
          arrange([
            hotels,
            flights,
            answer(3, "A", "some hotels"),
            answer(4, "B", "some flights"),
          ]),
        ),
      ).toEqual([
        ["hotels", "some hotels"],
        ["flights", "some flights"],
      ]);
    });

    it("leaves the other open when only one returned", () => {
      expect(
        answers(arrange([hotels, flights, answer(3, "A", "some hotels")])),
      ).toEqual([
        ["hotels", "some hotels"],
        ["flights", undefined],
      ]);
    });

    it("never gives a hand-off a return that names a different one", () => {
      expect(
        answers(arrange([hotels, answer(2, "gone", "someone else's")])),
      ).toEqual([
        ["hotels", undefined],
        [undefined, "someone else's"],
      ]);
    });

    it("files a tool call by its own delegation_id, and by the latest open without one", () => {
      const call = (seq, id) => ({
        seq,
        type: "tool_call",
        worker_id: "searcher",
        tool: "t",
        at: at(seq),
        ...(id ? { delegation_id: id } : {}),
      });
      const tree = arrange([hotels, flights, call(3, "A"), call(4)]);
      expect(tree.items.map((one) => one.items.map((i) => i.key))).toEqual([
        [3],
        [4],
      ]);
    });
  });

  it("takes a hand-off's missing start from its first step, and says when overlap can't be known", () => {
    const known = arrange([
      { seq: 1, type: "delegated", worker_id: "a", at: at(1) },
      { seq: 2, type: "returned", worker_id: "a", at: at(5) },
      { seq: 3, type: "delegated", worker_id: "b", at: null },
      { seq: 4, type: "tool_call", worker_id: "b", tool: "t", at: at(3) },
      { seq: 5, type: "returned", worker_id: "b", at: at(6) },
    ]);
    expect(known).toMatchObject({ parallel: 2, overlapUnknown: false });

    const unknown = arrange([
      { seq: 1, type: "delegated", worker_id: "a", at: at(1) },
      { seq: 2, type: "returned", worker_id: "a", at: at(5) },
      { seq: 3, type: "delegated", worker_id: "b", at: null },
      { seq: 4, type: "returned", worker_id: "b", at: at(6) },
    ]);
    expect(unknown).toMatchObject({ parallel: 0, overlapUnknown: true });
  });

  it("keeps the coordinator's own call at the top after control passes back to it", () => {
    const tree = arrange([
      { seq: 1, type: "transferred", worker_id: null, at: at(1) },
      { seq: 2, type: "tool_call", worker_id: null, tool: "t", at: at(2) },
    ]);
    expect(tree.items.map((one) => one.kind)).toEqual(["handoff", "call"]);
  });

  it("falls back to the latest open hand-off for a return with no delegation_id", () => {
    const tree = arrange([
      { seq: 1, type: "delegated", worker_id: "a", at: at(1) },
      { seq: 2, type: "delegated", worker_id: "a", at: at(2) },
      { seq: 3, type: "returned", worker_id: "a", at: at(3) },
    ]);
    expect(tree.items.map((one) => one.returned?.seq)).toEqual([undefined, 3]);
  });
});

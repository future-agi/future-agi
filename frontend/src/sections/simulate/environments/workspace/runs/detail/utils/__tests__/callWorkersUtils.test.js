import { describe, it, expect } from "vitest";
import {
  asShown,
  handOffCounts,
  handOffEnd,
  handOffRows,
  isLong,
  nest,
  seconds,
  turnLabel,
} from "../callWorkersUtils";

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

describe("nest", () => {
  it("puts each step under the hand-off its parent names and keeps the API's summary", () => {
    const tree = nest({
      steps: [
        {
          kind: "handoff",
          key: 1,
          parent: null,
          depth: 1,
          worker_id: "lead",
          transfer: false,
          handoff: { seq: 1 },
          returned: { seq: 5 },
          parallel: false,
          turn: 1,
        },
        {
          kind: "handoff",
          key: 2,
          parent: 1,
          depth: 2,
          worker_id: "helper",
          transfer: false,
          handoff: { seq: 2 },
          returned: null,
          parallel: true,
        },
        {
          kind: "tool_call",
          key: 3,
          parent: 2,
          depth: 2,
          worker_id: "helper",
          call: { seq: 3, tool: "t" },
        },
        {
          kind: "tool_call",
          key: 6,
          parent: null,
          depth: 0,
          worker_id: null,
          call: { seq: 6, tool: "reply" },
          turn: 1,
        },
      ],
      turns: [{ turn: 1, request: null, response: null }],
      depth: 2,
      parallel: 1,
      overlap_unknown: true,
      worker_depths: { lead: 1, helper: 2 },
    });

    const [lead, reply] = tree.items;
    expect(lead).toMatchObject({
      kind: "handoff",
      worker_id: "lead",
      depth: 1,
      returned: { seq: 5 },
    });
    const [helper] = lead.items;
    expect(helper).toMatchObject({
      worker_id: "helper",
      parallel: true,
      delegated: { seq: 2 },
    });
    expect(helper.items.map((item) => item.event.tool)).toEqual(["t"]);
    expect(reply).toMatchObject({ kind: "call", key: 6 });
  });

  it("puts each top-level step in its turn, with what it holds, and a step in no recorded turn apart", () => {
    const handOff = (key, parent, turn) => ({
      kind: "handoff",
      key,
      parent,
      depth: parent === null ? 1 : 2,
      worker_id: `w${key}`,
      turn,
    });
    const tree = nest({
      steps: [
        handOff(1, null, 1),
        handOff(2, 1, 1),
        handOff(3, null, 2),
        handOff(4, null, null),
      ],
      turns: [
        { turn: 1, request: { text: "a" }, response: null },
        { turn: 2, request: { text: "b" }, response: null },
        { turn: null, request: null, response: null },
      ],
    });
    expect(
      tree.rounds.map((round) => [
        round.turn,
        round.items.map((item) => [item.key, item.items.map((one) => one.key)]),
      ]),
    ).toEqual([
      [1, [[1, [2]]]],
      [2, [[3, []]]],
      [null, [[4, []]]],
    ]);
  });

  it("names a turn the record does not say as not recorded", () => {
    const rounds = [{ turn: 1 }, { turn: null }];
    expect(rounds.map((round) => turnLabel(round, rounds))).toEqual([
      "Turn 1 · ",
      "Turn not recorded · ",
    ]);
  });

  it("reads a record with no steps as an empty tree", () => {
    expect(nest({})).toEqual({ items: [], rounds: [] });
  });
});

describe("hand-off rows", () => {
  const failedTool = {
    kind: "call",
    key: 9,
    event: { tool: "issue_refund", status: "error" },
  };
  const block = (key, extra) => ({
    kind: "handoff",
    key,
    worker_id: `w${key}`,
    items: [],
    ...extra,
  });

  it("lists each hand-off followed by the ones it made, leaving tool calls out", () => {
    const nested = block(2);
    const tree = [block(1, { items: [failedTool, nested] }), block(3)];
    expect(handOffRows(tree).map((one) => one.key)).toEqual([1, 2, 3]);
  });

  it("reads how a hand-off ended from its return, a control pass from its tools, and a cut record as unknown", () => {
    const ok = { status: "ok" };
    const error = { status: "error" };
    expect(handOffEnd(block(1, { returned: ok }))).toBe("finished");
    // The worker recovered from its failed tool and returned.
    expect(handOffEnd(block(1, { returned: ok, items: [failedTool] }))).toBe(
      "finished",
    );
    expect(handOffEnd(block(1, { returned: error }))).toBe("failed");
    expect(handOffEnd(block(1, { transfer: true }))).toBe("passed");
    expect(handOffEnd(block(1, { transfer: true, items: [failedTool] }))).toBe(
      "failed",
    );
    expect(handOffEnd(block(1))).toBe("no_return");
    expect(handOffEnd(block(1), true)).toBe("cut_off");
  });

  it("counts a worker handed work twice once, and a control pass on its own", () => {
    const rows = [
      block(1, { worker_id: "a", returned: { status: "ok" } }),
      block(2, { worker_id: "a", returned: { status: "error" } }),
      block(3, { worker_id: "b" }),
      block(4, { worker_id: "c", transfer: true }),
    ];
    expect(handOffCounts(rows, false)).toEqual({
      handOffs: 4,
      workers: 3,
      finished: 1,
      didNotFinish: 2,
      passed: 1,
      cutOff: 0,
    });
  });
});

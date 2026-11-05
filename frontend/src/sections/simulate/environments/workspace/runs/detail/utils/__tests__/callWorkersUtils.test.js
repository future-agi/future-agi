import { describe, it, expect } from "vitest";
import { asShown, isLong, nest, seconds } from "../callWorkersUtils";

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
        },
      ],
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
    expect(tree).toMatchObject({
      depth: 2,
      parallel: 1,
      overlapUnknown: true,
      depthOf: { lead: 1, helper: 2 },
    });
  });

  it("reads a record with no steps as an empty tree", () => {
    expect(nest({})).toEqual({
      items: [],
      depth: 0,
      parallel: 0,
      overlapUnknown: false,
      depthOf: {},
    });
  });
});

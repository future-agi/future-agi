import { describe, it, expect } from "vitest";
import {
  asMarkdown,
  callEvents,
  handOffEvents,
  isLong,
  seconds,
  stepsByKey,
} from "../callWorkersUtils";

const names = {
  coordinator: "Coordinator",
  ofStep: (step) => step.worker_id,
  ofWorker: (id) => id || "Coordinator",
};

// The API's record, as it sends it: a lead's hand-off holding a helper's, which made one tool call.
const record = (helperEnd = "no_return") => ({
  steps: [
    {
      kind: "handoff",
      key: 1,
      parent: null,
      depth: 1,
      worker_id: "lead",
      transfer: false,
      handoff: { at: "2026-10-31T12:00:01+00:00", task_shown: null },
      returned: {
        at: "2026-10-31T12:00:09+00:00",
        result_shown: { format: "text", text: "done" },
      },
      end: "finished",
      did_not_finish: false,
      children: [2],
      turn: 1,
      relationship: "observed",
    },
    {
      kind: "handoff",
      key: 2,
      parent: 1,
      depth: 2,
      worker_id: "helper",
      transfer: false,
      handoff: { at: "2026-10-31T12:00:02+00:00", by: "lead" },
      returned: null,
      end: helperEnd,
      did_not_finish: helperEnd === "no_return",
      children: [3],
      turn: 1,
      relationship: "observed",
    },
    {
      kind: "tool_call",
      key: 3,
      parent: 2,
      depth: 2,
      worker_id: "helper",
      call: { tool: "lookup", worker_id: "helper", attribution: "reported" },
      turn: 1,
      relationship: "observed",
    },
  ],
  turns: [
    {
      turn: 1,
      request: { text: "hi", shown: { format: "text", text: "hi" } },
      response: null,
      children: [1],
      calls_outside: 0,
      handoffs: [1, 2],
      expects_messages: true,
    },
  ],
  counts: { hand_offs: 2 },
});

describe("seconds", () => {
  it("reads an API time, and anything else as unknown", () => {
    expect(seconds("2026-10-31T12:00:05+00:00")).toBe(
      Date.UTC(2026, 9, 31, 12, 0, 5) / 1000,
    );
    expect(seconds(null)).toBeNull();
    expect(seconds("not a time")).toBeNull();
  });
});

describe("asMarkdown", () => {
  it("fences a text the API marked as data and leaves prose as it came", () => {
    const data = asMarkdown({ format: "code", text: '{\n  "a": 1\n}' });
    expect(data.startsWith("```")).toBe(true);
    expect(data).toContain('"a": 1');
    expect(asMarkdown({ format: "text", text: "[Note] sent" })).toBe(
      "[Note] sent",
    );
  });

  it("keeps code that holds a fence of its own inside one block", () => {
    const text = "```\nnested\n```";
    const shown = asMarkdown({ format: "code", text });
    const fence = shown.split("\n")[0];
    expect(fence.length).toBeGreaterThan(3);
    expect(shown.endsWith(fence)).toBe(true);
  });
});

describe("isLong", () => {
  it("folds a long answer and leaves a short one open", () => {
    expect(isLong("short")).toBe(false);
    expect(isLong("line\n".repeat(50))).toBe(true);
  });
});

describe("steps from the API", () => {
  it("shows a hand-off's own calls and ending, not the hand-offs it made", () => {
    const data = record();
    const byKey = stepsByKey(data);
    const lead = handOffEvents(byKey[1], byKey, names);
    expect(lead.some((event) => event.call)).toBe(false);
    const helper = handOffEvents(byKey[2], byKey, names);
    expect(helper.filter((event) => event.call)).toHaveLength(1);
  });

  it("follows the API's end for a hand-off that never returned", () => {
    const ending = (end) => {
      const data = record(end);
      const byKey = stepsByKey(data);
      return handOffEvents(byKey[2], byKey, names).at(-1);
    };
    expect(ending("no_return").bad).toBe(true);
    expect(ending("cut_off").bad).toBeFalsy();
    expect(ending("cut_off").note).not.toBe(ending("no_return").note);
  });

  it("reads every step of a call in order: the request, each hand-off and what it held, then the reply", () => {
    const data = record();
    const keys = callEvents(data, stepsByKey(data), names).map(
      (event) => event.key,
    );
    expect(keys).toEqual([
      "request-1",
      "handoff-1",
      "handoff-2",
      "call-3",
      "end-2",
      "end-1",
      "reply-1",
    ]);
  });
});

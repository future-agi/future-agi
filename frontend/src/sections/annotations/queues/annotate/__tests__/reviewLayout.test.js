import { describe, expect, it } from "vitest";
import { checklistOf, taskDetailsSummary, taskOneLiner } from "../reviewLayout";

const CHECKS = [
  {
    key: "order_found",
    name: "Order found",
    type: "check",
    assert: { tool: "lookup_order" },
    description: "lookup_order returned A-4108",
    draft: false,
  },
  {
    key: "reply_rubric",
    name: "Task reply rubric v1",
    type: "judge",
    description: "acknowledges, says what was done",
    draft: true,
  },
];

describe("taskOneLiner", () => {
  it("prefers the field that holds the ask", () => {
    expect(
      taskOneLiner({
        order_id: "A-4108",
        customer_goal: "Ship it to 9 Birch Court",
        instruction: "  Can it go to my new place?  ",
      }),
    ).toEqual({ name: "instruction", text: "Can it go to my new place?" });
  });

  it("falls back to the first plain-text field, skipping JSON", () => {
    expect(
      taskOneLiner({ start_state: '{"orders": []}', note: "Late parcel" }),
    ).toEqual({ name: "note", text: "Late parcel" });
  });

  it("reads the last user turn of a chat", () => {
    const chat = [
      { role: "user", content: "Hi" },
      { role: "assistant", content: "Hello" },
      { role: "user", content: "Where is A-9014?" },
    ];
    expect(taskOneLiner({ conversation: JSON.stringify(chat) }).text).toBe(
      "Where is A-9014?",
    );
  });

  it("is null when nothing is readable", () => {
    expect(taskOneLiner({})).toBeNull();
    expect(taskOneLiner({ a: "", b: "[]" })).toBeNull();
  });
});

describe("checklistOf", () => {
  it("turns a list of check objects (or its JSON) into a checklist", () => {
    for (const value of [CHECKS, JSON.stringify(CHECKS)]) {
      const list = checklistOf({ checks: value });
      expect(list.field).toBe("checks");
      expect(list.items.map((c) => c.name)).toEqual([
        "Order found",
        "Task reply rubric v1",
      ]);
      expect(list.items[1]).toMatchObject({ type: "judge", draft: true });
    }
  });

  it("is null for plain expected values", () => {
    expect(checklistOf({ expected_reply: "Refund in 5–7 days" })).toBeNull();
    expect(checklistOf({ tools: ["lookup_order"] })).toBeNull();
    expect(checklistOf(undefined)).toBeNull();
  });
});

describe("taskDetailsSummary", () => {
  it("counts inputs, checks and metadata", () => {
    expect(
      taskDetailsSummary({
        input: { a: "x", b: "y", c: "" },
        expected: { checks: CHECKS },
        metadata: { tier: "pro", channel: "chat", ticket: "T-1" },
      }),
    ).toBe("2 input fields · 2 checks · 3 metadata");
  });

  it("counts expected fields when they are not checks", () => {
    expect(
      taskDetailsSummary({ input: { a: "x" }, expected: { reply: "ok" } }),
    ).toBe("1 input field · 1 expected field");
  });
});

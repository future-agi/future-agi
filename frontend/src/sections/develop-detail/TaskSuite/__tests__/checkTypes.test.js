import { describe, expect, it } from "vitest";
import {
  actualText,
  applyToCategory,
  buildCheck,
  checkSentence,
  checkTypeOf,
  draftFromCheck,
  endStateExpression,
  evaluateEndState,
  hasDraft,
  markReviewed,
  normalizePath,
  parseChecks,
  startStateIssues,
  startStateWarning,
  validateDraft,
} from "../checkTypes";

const T26_CHECKS = [
  {
    key: "order_found",
    name: "Order found",
    type: "check",
    assert: { tool: "lookup_order", op: "returned", value: "A-8830" },
    draft: true,
  },
  {
    key: "refund_once",
    name: "Refund issued once",
    type: "check",
    assert: { path: "refunds[order=A-8830]", op: "count_eq", value: 1 },
    draft: true,
  },
  {
    key: "refund_amount",
    name: "Refund amount",
    type: "check",
    assert: { path: "refunds[order=A-8830].amount", op: "eq", value: 149 },
    draft: true,
  },
  {
    key: "timeline_stated",
    name: "Timeline stated",
    type: "check",
    assert: { path: "reply", op: "mentions", value: "5–7 business days" },
    draft: true,
  },
  {
    key: "reply_rubric",
    name: "Task reply rubric v1",
    type: "judge",
    assert: { judge: "Task reply rubric v1", model: "gpt-4o" },
    draft: true,
  },
];
const START = {
  orders: [{ id: "A-8830", status: "delivered", items: [], shipping: {} }],
  customers: [{ id: "cust-2740" }],
  refunds: [],
  tickets: [],
};
const SCHEMA = {
  type: "object",
  required: ["orders", "customers", "refunds", "tickets"],
  properties: {
    orders: { type: "array" },
    customers: { type: "array" },
    refunds: { type: "array" },
    tickets: { type: "array" },
  },
};

describe("check types", () => {
  it("reads the world's checks as the four types", () => {
    const checks = parseChecks(JSON.stringify(T26_CHECKS));
    expect(checks.map(checkTypeOf)).toEqual([
      "tool_call",
      "end_state",
      "end_state",
      "end_state",
      "rubric",
    ]);
    expect(hasDraft(checks)).toBe(true);
    expect(hasDraft(markReviewed(checks))).toBe(false);
    expect(
      checkTypeOf({ kind: "python", assert: { code: "def check()" } }),
    ).toBe("python");
    expect(parseChecks("not json")).toEqual([]);
  });

  it("phrases checks as sentences", () => {
    expect(normalizePath("refunds[order=A-8830]")).toBe(
      'refunds[?(@.order=="A-8830")]',
    );
    expect(checkSentence(T26_CHECKS[1])).toBe(
      'count(refunds[?(@.order=="A-8830")]) == 1',
    );
    expect(checkSentence(T26_CHECKS[2])).toBe(
      'refunds[?(@.order=="A-8830")].amount == 149',
    );
    expect(checkSentence(T26_CHECKS[0])).toBe('lookup_order returned "A-8830"');
    expect(checkSentence(T26_CHECKS[3])).toBe(
      'reply mentions "5–7 business days"',
    );
    expect(checkSentence(T26_CHECKS[4])).toBe(
      "Task reply rubric v1 on the final reply",
    );
    expect(
      checkSentence(
        buildCheck({
          ...draftFromCheck(null),
          type: "tool_call",
          key: "refund_issued",
          tool: "issue_refund",
          toolMode: "called",
          timesOp: "exactly",
          timesN: "1",
          argName: "order_id",
          argValue: "A-8830",
        }),
      ),
    ).toBe('issue_refund called exactly 1 time with order_id = "A-8830"');
    expect(
      checkSentence(
        buildCheck({
          ...draftFromCheck(null),
          type: "tool_call",
          key: "no_cancel",
          tool: "cancel_item",
          toolMode: "not_called",
        }),
      ),
    ).toBe("cancel_item not called");
  });

  it("evaluates end state checks on a state", () => {
    const fields = {
      path: 'refunds[?(@.order_id=="A-8830")]',
      aggregate: "count",
      expected: 1,
    };
    expect(endStateExpression(fields)).toBe(
      'count(refunds[?(@.order_id=="A-8830")]) == 1',
    );
    const onStart = evaluateEndState(fields, JSON.stringify(START));
    expect(onStart).toEqual({ ok: true, actual: 0, pass: false });
    expect(actualText(onStart)).toBe("0");
    const end = { ...START, refunds: [{ order_id: "A-8830", amount: 149 }] };
    expect(evaluateEndState(fields, end).pass).toBe(true);
    expect(
      evaluateEndState(
        { path: "orders[0].status", aggregate: "value", expected: "delivered" },
        START,
      ).pass,
    ).toBe(true);
    expect(
      evaluateEndState(
        { path: "tickets", aggregate: "count", expected: 0 },
        START,
      ),
    ).toEqual({ ok: true, actual: 0, pass: true });
    expect(
      evaluateEndState(
        { path: "refunds[?(@.order_id==", aggregate: "count", expected: 1 },
        START,
      ).error,
    ).toBe("Invalid path");
  });

  it("validates drafts with a reason", () => {
    const d = {
      ...draftFromCheck(null),
      key: "refund_once",
      path: 'refunds[?(@.order_id=="A-8830")]',
      aggregate: "count",
      expected: "1",
    };
    expect(validateDraft(d).valid).toBe(true);
    expect(validateDraft({ ...d, key: "" }).reason).toBe("Name the check");
    expect(validateDraft({ ...d, key: "Refund Once" }).field).toBe("key");
    expect(validateDraft(d, { existingKeys: ["refund_once"] }).reason).toBe(
      "A check named refund_once already exists",
    );
    expect(validateDraft({ ...d, path: "refunds[?(" }).reason).toBe(
      "Invalid path",
    );
    expect(validateDraft({ ...d, expected: "" }).reason).toBe(
      "Enter the expected value",
    );
    expect(validateDraft({ ...d, expected: "1.5" }).reason).toBe(
      "A count is a whole number",
    );
    expect(validateDraft({ ...d, type: "tool_call", tool: "" }).reason).toBe(
      "Pick a tool",
    );
    expect(validateDraft({ ...d, type: "rubric", judge: "" }).reason).toBe(
      "Pick an eval",
    );
    expect(validateDraft({ ...d, type: "python", code: "x = 1" }).field).toBe(
      "code",
    );
    expect(validateDraft({ ...d, type: "python" }).valid).toBe(true);
  });

  it("round-trips a draft through buildCheck", () => {
    const d = {
      ...draftFromCheck(null),
      key: "refund_once",
      name: "Refund once",
      path: 'refunds[?(@.order_id=="A-8830")]',
      aggregate: "count",
      expected: "1",
    };
    const check = buildCheck(d);
    expect(check).toMatchObject({
      key: "refund_once",
      type: "check",
      kind: "end_state",
      draft: true,
      assert: { aggregate: "count", expected: 1 },
    });
    const again = draftFromCheck(check);
    expect(again.path).toBe('refunds[?(@.order_id=="A-8830")]');
    expect(again.expected).toBe("1");
    const rubric = buildCheck({
      ...d,
      type: "rubric",
      key: "tone",
      judge: "Tone",
      scope: "transcript",
    });
    expect(checkSentence(rubric)).toBe("Tone on the whole transcript");
  });

  it("applies a check to every row of a category", () => {
    const rows = [
      { row_id: "r1", category: "refund", checks: JSON.stringify(T26_CHECKS) },
      { row_id: "r2", category: "refund", checks: "[]" },
      { row_id: "r3", category: "order_status", checks: "[]" },
    ];
    const check = {
      key: "refund_once",
      name: "x",
      assert: { path: "refunds", aggregate: "count", expected: 1 },
    };
    const out = applyToCategory(rows, check, "refund");
    expect(out.map((o) => o.rowId)).toEqual(["r1", "r2"]);
    expect(JSON.parse(out[0].value)).toHaveLength(5);
    expect(JSON.parse(out[1].value)).toHaveLength(1);
  });

  it("checks the start state against the environment schema", () => {
    expect(startStateIssues(START, SCHEMA)).toBeNull();
    const { refunds: _r, ...noRefunds } = START;
    const issues = startStateIssues(JSON.stringify(noRefunds), SCHEMA);
    expect(issues.missing).toEqual(["refunds"]);
    expect(startStateWarning(issues)).toBe(
      "Start state doesn't match the environment schema: missing refunds",
    );
    expect(
      startStateIssues({ ...START, tickets: {} }, SCHEMA).wrongType,
    ).toEqual([{ key: "tickets", expected: "array", actual: "object" }]);
    expect(startStateIssues(START, null)).toBeNull();
  });
});

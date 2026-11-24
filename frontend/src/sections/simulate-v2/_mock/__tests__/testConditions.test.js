import { describe, expect, it } from "vitest";
import { getEnvironment } from "../environments";
import {
  CALLS,
  approvalActionsForConditions,
  conditionLabelOf,
  normalizeCondition,
} from "../testConditions";

const env = getEnvironment("env-chat-banking");

describe("test conditions", () => {
  it("fail one chosen call — no every option", () => {
    expect(CALLS.map((c) => c.id)).not.toContain("every");
    expect(CALLS[0]).toEqual({ id: 1, label: "Call 1" });
  });

  it("read older first and every saves as call 1", () => {
    expect(
      normalizeCondition({ tool: "t", kind: "error", call: "first" }).call,
    ).toBe(1);
    const every = normalizeCondition({
      tool: "t",
      kind: "error",
      call: "every",
    });
    expect(every.call).toBe(1);
    expect(every.wasEvery).toBe(true);
    expect(
      conditionLabelOf({ tool: "t", kind: "error", call: "every" }),
    ).toMatch(/call 1 \(saved as every call\)/);
  });

  it("offer the environment's approval actions for Needs approval first", () => {
    const names = approvalActionsForConditions(env, {});
    expect(names).toContain("approvals.change");
    expect(names).toContain("approve_freeze_card");
    expect(names.every((n) => /^approve_|^approvals\./.test(n))).toBe(true);
    expect(
      conditionLabelOf({
        tool: "freeze_card",
        kind: "permission",
        call: 2,
        approval: "approve_freeze_card",
      }),
    ).toBe("freeze_card · needs approval first · approve_freeze_card · call 2");
  });
});

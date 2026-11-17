import { describe, expect, it } from "vitest";

import { matchReason, reasonText } from "../TriggeredRequestsTab";

// A row with no error message (a block recorded with its checks) reads its
// Why from guardrail_results, where the gateway keeps each check's reason.
describe("Triggered requests Why", () => {
  it("reads the reason from guardrail_results when there is no error message", () => {
    expect(
      matchReason({
        status_code: 200,
        guardrail_results: {
          action: "block",
          checks: [
            { name: "pii", action: "block", message: "Email address found" },
            { name: "toxicity", action: "pass" },
          ],
        },
      }),
    ).toBe("Email address found");
  });

  it("reads guardrail_results stored as a JSON string, with details as the message", () => {
    expect(
      matchReason({
        guardrail_results: JSON.stringify([
          { name: "leak", action: "block", details: "Key-like string" },
        ]),
      }),
    ).toBe("Key-like string");
  });

  it("keeps the block message first when there is one", () => {
    expect(
      matchReason({
        error_message: "Request blocked by guardrail: pii — Email found",
        guardrail_results: { checks: [{ action: "block", message: "x" }] },
      }),
    ).toBe("Email found");
  });

  it("is empty when nothing says why, and the cell reads the empty mark", () => {
    expect(matchReason({ guardrail_results: "not json" })).toBe("");
    expect(reasonText("")).toBe("—");
    expect(reasonText("Email found")).toBe("Email found");
  });
});

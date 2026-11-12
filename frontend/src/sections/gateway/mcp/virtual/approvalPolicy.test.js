import { describe, expect, it } from "vitest";
import { approvalForm, approvalPatch } from "./approvalPolicy";

describe("approval policy preserves explicit user choice", () => {
  it("does not silently add a policy to an unconfigured server", () => {
    expect(approvalPatch(approvalForm())).toEqual({ require: "none" });
  });
  it("round-trips selected tools and once-only expiry", () => {
    const policy = {
      require: "listed",
      tools: ["mock/delete_file"],
      validity: "once",
      ttl_seconds: 120,
    };
    expect(approvalPatch(approvalForm(policy))).toEqual(policy);
  });
  it.each(["0", "86401", "1.5", "NaN"])("rejects invalid expiry %s", (ttl) => {
    expect(() =>
      approvalPatch({
        require: "listed",
        tools: "mock/*",
        validity: "once",
        ttl,
      }),
    ).toThrow();
  });
});

import { describe, expect, it } from "vitest";
import { serializeTraceTags } from "../traceTagPayload";

describe("serializeTraceTags", () => {
  it("converts rich trace tags to the string payload expected by the API", () => {
    expect(
      serializeTraceTags([
        { name: "customer-escalation", color: "#EF4444" },
        { name: "needs-review", color: "#3B82F6" },
      ]),
    ).toEqual(["customer-escalation", "needs-review"]);
  });

  it("keeps existing string tags and supports clearing all tags", () => {
    expect(serializeTraceTags(["existing-tag"])).toEqual(["existing-tag"]);
    expect(serializeTraceTags([])).toEqual([]);
  });
});

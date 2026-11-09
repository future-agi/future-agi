import { describe, expect, it } from "vitest";
import { ModelHubDevelopsEditDatasetBehaviorUpdateBody } from "src/generated/api-contracts/api.zod";

describe("generated dataset settings contract", () => {
  it("accepts the tags accepted by the backend", () => {
    expect(
      ModelHubDevelopsEditDatasetBehaviorUpdateBody.safeParse({
        dataset_name: "Regression examples",
        description: "Support examples",
        tags: ["golden", "support.v2", "tag-1", "tag_2"],
      }).success,
    ).toBe(true);
  });
  it("rejects invalid tags and overlong descriptions", () => {
    for (const value of [
      { tags: ["Invalid Tag"] },
      { tags: ["a".repeat(31)] },
      { description: "x".repeat(501) },
    ]) {
      expect(
        ModelHubDevelopsEditDatasetBehaviorUpdateBody.safeParse(value).success,
      ).toBe(false);
    }
  });
});

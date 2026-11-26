import { describe, expect, it } from "vitest";
import { buildSourceReview as buildImportReview } from "../importSourceReview";
import { buildSourceReview as buildExistingReview } from "../sourceReview";

const draft = {
  draft_digest: "sha256:draft",
  sources: [{ pin: { source_id: "policy", role: "reference" } }],
  proposals: {},
};

describe.each([
  ["new Environment", buildImportReview],
  ["existing Environment", buildExistingReview],
])("%s source review", (_, buildSourceReview) => {
  it("makes approved source facts visible to the tested agent by default", () => {
    expect(buildSourceReview(draft).reference_reviews).toEqual([
      {
        source_id: "policy",
        audience: { tested_agent: true, participant_ids: [] },
      },
    ]);
  });
});

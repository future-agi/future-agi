import { describe, expect, it } from "vitest";
import { absentTemplates } from "../absentPathGuard";
import { MADE_REAL, MADE_REAL_PATHS } from "../madeReal";
import { SURFACE_BY_ID } from "../launchSurfaces";

const IMPROVE = "/model-hub/eval-templates/{template_id}/improve/";

describe("evals.agreementTools improve endpoint (EVAL-E2E-083)", () => {
  it("keeps the tools and the improve endpoint unclaimed until a walk passes", () => {
    // Improve needs a managed LLM route no stack activates yet, so the tools
    // stay hidden and the improve path stays refused until a walk passes.
    expect(MADE_REAL.EVAL).not.toContain("evals.agreementTools");
    // The pinned prototype path belongs to the tools' stored-report parent.
    expect(MADE_REAL.EVAL).toContain("evals.agreement");
    expect(MADE_REAL_PATHS.EVAL).not.toContain(IMPROVE);
  });

  it("limits the existing tools contract to served judge improvement", () => {
    expect(SURFACE_BY_ID["evals.agreementTools"]).toMatchObject({
      label: "Improve judge instructions against human labels",
      madeRealBy: "EVAL",
      ships: "hidden",
      parent: "evals.agreement",
      claimRequired: true,
      options: [],
    });
    expect(SURFACE_BY_ID["evals.agreementTools"].why).toMatch(
      /does not claim compare, training or confidence/i,
    );
  });

  it("refuses the improve call through the absent-path guard", () => {
    expect(absentTemplates()).toContain(IMPROVE);
  });
});

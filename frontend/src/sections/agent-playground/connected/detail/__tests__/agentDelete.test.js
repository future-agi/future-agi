import { describe, it, expect } from "vitest";
import { deleteBlockedReason } from "../agentDelete";

describe("deleteBlockedReason", () => {
  it("lets an agent nothing tests be deleted", () => {
    expect(
      deleteBlockedReason({ environmentCount: 0, testedByEnvironment: false }),
    ).toBeNull();
  });

  it("names how many environments its tab lists", () => {
    expect(
      deleteBlockedReason({ environmentCount: 2, testedByEnvironment: true }),
    ).toBe("Tested by 2 environments. Delete those environments first.");
  });

  it("keeps an agent its tab lists nothing for, and asks for no deletes", () => {
    expect(
      deleteBlockedReason({ environmentCount: 0, testedByEnvironment: true }),
    ).toBe("Still tested by an environment, so it can't be deleted.");
  });
});

import { describe, expect, it } from "vitest";

import { withoutReplayForRestrictedOrgs } from "../replayAccess";

const ACTIONS = [
  { id: "replay", label: "Replay Sessions" },
  { id: "annotation-queue", label: "Add to annotation queue" },
];

describe("withoutReplayForRestrictedOrgs", () => {
  it("drops replay for an org restricted to Environments", () => {
    expect(
      withoutReplayForRestrictedOrgs(ACTIONS, true).map((a) => a.id),
    ).toEqual(["annotation-queue"]);
  });

  it("keeps every action for other orgs", () => {
    expect(withoutReplayForRestrictedOrgs(ACTIONS, false)).toEqual(ACTIONS);
  });
});

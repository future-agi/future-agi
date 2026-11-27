import { describe, it, expect } from "vitest";
import { ENV_STATUS } from "src/sections/simulate/environments/myEnvironments.constants";
import { canFollow } from "../environmentPolicy";

const pinned = (status) => ({
  policy: "pinned",
  agentVersion: { id: "v1", number: 1 },
  status,
});

describe("canFollow", () => {
  it("offers to follow production on a pinned row that can still move", () => {
    for (const status of [
      ENV_STATUS.READY,
      ENV_STATUS.RUNNING,
      ENV_STATUS.BUILDING,
    ]) {
      expect(canFollow(pinned(status))).toBe(true);
    }
  });

  it("offers nothing on a failed or cancelled row", () => {
    expect(canFollow(pinned(ENV_STATUS.FAILED))).toBe(false);
    expect(canFollow(pinned(ENV_STATUS.CANCELLED))).toBe(false);
  });

  it("offers nothing to a row that already follows or has no version", () => {
    expect(canFollow({ ...pinned(ENV_STATUS.READY), policy: "follow" })).toBe(
      false,
    );
    expect(canFollow({ ...pinned(ENV_STATUS.READY), agentVersion: null })).toBe(
      false,
    );
  });
});

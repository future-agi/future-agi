import { describe, expect, it } from "vitest";

import { FRAME_ALLOW, FRAME_CLASS } from "../frame-attributes";

const permissions = FRAME_ALLOW.split(";").map((permission) =>
  permission.trim(),
);

describe("shared Omega frame attributes", () => {
  it("J07-002-01 delegates clipboard-write to the frame's src origin", () => {
    expect(permissions).toContain("clipboard-write");
  });

  it("J07-002-02 preserves local-network-access", () => {
    expect(permissions).toContain("local-network-access");
  });

  it("J07-002-03 preserves loopback-network", () => {
    expect(permissions).toContain("loopback-network");
  });

  it("J07-002-04 preserves the session-replay exclusion classes", () => {
    expect(FRAME_CLASS).toBe("ph-no-capture rr-block");
  });

  it("J07-002-05 does not delegate clipboard-read", () => {
    const features = permissions.map((permission) => permission.split(/\s+/)[0]);
    expect(features).not.toContain("clipboard-read");
  });
});

import { describe, it, expect } from "vitest";
import { shortPath } from "../parts/shortPath";

describe("shortPath", () => {
  it("drops everything before the artifacts directory", () => {
    expect(
      shortPath(
        "/Users/apple/Documents/agent-learning-kit/artifacts/sessions/drive-thru-verify/scenarios/late-night-menu-swap"
      )
    ).toBe("artifacts/sessions/drive-thru-verify/scenarios/late-night-menu-swap");
  });

  it("works for a container path just the same", () => {
    expect(shortPath("/app/artifacts/sessions/abc")).toBe("artifacts/sessions/abc");
  });

  it("keeps the tail when there is no artifacts directory to cut at", () => {
    expect(shortPath("/var/data/some/other/place")).toBe("…/some/other/place");
  });

  it("leaves a short path alone", () => {
    expect(shortPath("sessions/abc")).toBe("sessions/abc");
  });

  it("says nothing when there is no path", () => {
    expect(shortPath("")).toBe("");
    expect(shortPath(undefined)).toBe("");
  });
});

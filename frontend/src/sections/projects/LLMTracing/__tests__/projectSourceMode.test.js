import { describe, expect, it } from "vitest";
import { isTraceListProjectReady } from "../projectSourceMode";

describe("isTraceListProjectReady", () => {
  it.each(["prototype", "observe", "demo", "future-direct-write"])(
    "enables exact lists for resolved non-simulator source %s",
    (projectSource) => {
      expect(
        isTraceListProjectReady({ projectId: "project-1", projectSource }),
      ).toBe(true);
    },
  );

  it.each([undefined, null, ""])(
    "waits for project source resolution before enabling (%s)",
    (projectSource) => {
      expect(
        isTraceListProjectReady({ projectId: "project-1", projectSource }),
      ).toBe(false);
    },
  );

  it("keeps simulator projects on the voice-call list", () => {
    expect(
      isTraceListProjectReady({
        projectId: "project-1",
        projectSource: "simulator",
      }),
    ).toBe(false);
  });

  it("allows the user detail view to make its intentional org-scoped read", () => {
    expect(
      isTraceListProjectReady({
        projectId: null,
        projectSource: "observe",
        allowOrgScope: true,
      }),
    ).toBe(true);
  });

  it("does not enable a project-scoped list before its id resolves", () => {
    expect(
      isTraceListProjectReady({
        projectId: null,
        projectSource: "observe",
      }),
    ).toBe(false);
  });
});

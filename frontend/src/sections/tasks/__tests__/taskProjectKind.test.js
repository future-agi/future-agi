import { describe, expect, it } from "vitest";

import { PROJECT_SOURCE } from "src/utils/constants";
import {
  isTaskPreviewProjectKindReady,
  nextTaskRowTypeForProject,
} from "../taskProjectKind";

describe("task project-kind resolution", () => {
  it("does not rewrite voiceCalls while project details are pending", () => {
    expect(
      nextTaskRowTypeForProject({
        isProjectSelected: true,
        projectDetailsResolved: false,
        projectSource: undefined,
        rowType: "voiceCalls",
      }),
    ).toBeNull();
    expect(
      isTaskPreviewProjectKindReady({
        waitForProjectKind: true,
        projectDetailsResolved: false,
        projectSource: undefined,
        rowType: "voiceCalls",
      }),
    ).toBe(false);
  });

  it("starts one voice preview only after simulator kind and row type agree", () => {
    expect(
      nextTaskRowTypeForProject({
        isProjectSelected: true,
        projectDetailsResolved: true,
        projectSource: PROJECT_SOURCE.SIMULATOR,
        rowType: "spans",
      }),
    ).toBe("voiceCalls");
    expect(
      isTaskPreviewProjectKindReady({
        waitForProjectKind: true,
        projectDetailsResolved: true,
        projectSource: PROJECT_SOURCE.SIMULATOR,
        rowType: "spans",
      }),
    ).toBe(false);
    expect(
      isTaskPreviewProjectKindReady({
        waitForProjectKind: true,
        projectDetailsResolved: true,
        projectSource: PROJECT_SOURCE.SIMULATOR,
        rowType: "voiceCalls",
      }),
    ).toBe(true);
  });

  it("never rewrites the immutable row type of an existing task", () => {
    expect(
      nextTaskRowTypeForProject({
        isProjectSelected: true,
        projectDetailsResolved: true,
        projectSource: PROJECT_SOURCE.SIMULATOR,
        rowType: "spans",
        rowTypeLocked: true,
      }),
    ).toBeNull();
  });
});

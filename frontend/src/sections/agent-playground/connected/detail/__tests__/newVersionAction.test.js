import { describe, expect, it } from "vitest";
import { newVersionAction } from "../newVersionAction";

describe("newVersionAction", () => {
  it.each(["phone", "livekit"])("syncs a %s agent by editing it", (source) => {
    expect(newVersionAction({ source })).toMatchObject({
      label: "Sync",
      kind: "edit",
    });
  });

  it.each(["vapi", "retell", "github"])(
    "syncs a %s agent from where it lives",
    (source) => {
      expect(newVersionAction({ source })).toMatchObject({
        label: "Sync",
        kind: "sync",
      });
    },
  );

  it("syncs a code agent whatever its source", () => {
    expect(
      newVersionAction({ source: "other", agentKind: "source" }),
    ).toMatchObject({ kind: "sync" });
  });

  it("takes a new upload for an uploaded agent", () => {
    expect(newVersionAction({ source: "archive" })).toMatchObject({
      label: "Upload new version",
      kind: "upload",
    });
  });

  it("takes an edited prompt for anything else", () => {
    expect(newVersionAction({ source: "builtin" })).toMatchObject({
      label: "Add version",
      kind: "manual",
    });
    expect(newVersionAction({})).toMatchObject({ kind: "manual" });
  });
});

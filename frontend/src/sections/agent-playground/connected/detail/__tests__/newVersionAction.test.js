import { describe, expect, it } from "vitest";
import { newVersionAction } from "../newVersionAction";

describe("newVersionAction", () => {
  it.each(["phone", "livekit"])(
    "adds a %s agent's version by hand",
    (source) => {
      expect(newVersionAction({ source })).toMatchObject({
        label: "Add version",
        kind: "edit",
      });
    },
  );

  it.each(["vapi", "retell", "github"])(
    "syncs a %s agent from where it lives",
    (source) => {
      expect(newVersionAction({ source })).toMatchObject({
        label: "Sync",
        kind: "sync",
      });
    },
  );

  it("never offers Sync to a code agent whose source is not GitHub", () => {
    expect(
      newVersionAction({ source: null, agentKind: "source" }).kind,
    ).not.toBe("sync");
    expect(
      newVersionAction({ source: "archive", agentKind: "source" }),
    ).toMatchObject({ kind: "upload" });
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

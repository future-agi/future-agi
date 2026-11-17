// M6 (W4 §9 integration, W1 A gates + DatasetHeaderBar): the Experiments tab's
// run / edit / delete / download doors and the dataset header's Delete and
// New experiment follow the dataset's access, with the server's reasons.
import { describe, expect, it } from "vitest";
import {
  NO_DELETE,
  NO_EDIT,
  canRunOf,
  deleteTip,
  editTip,
  leaderboardGates,
  runTip,
} from "../leaderboardAccess";
import {
  VIEWER_DELETE,
  headerAccessDoors,
} from "../../DevelopBar/headerAccessDoors";

const reasons = {
  run: "You have Edit access to this dataset. You need run access. Ask Asha Rao.",
  download: "Viewers can download only when an admin grants it. Ask Asha Rao.",
};
const accessOf = (perms, me = {}) => ({
  can: { view: true, download: true, edit: true, run: true, ...perms },
  reason: (p) => reasons[p] ?? null,
  view: {
    me: { perms: { ...perms }, ...me },
    ask: [{ id: "a", name: "Asha Rao" }],
  },
});

describe("leaderboardGates", () => {
  it("Edit without run: pin and rename stay, rerun / stop / delete are refused with the reason", () => {
    const g = leaderboardGates(
      { roleEdit: true, roleDelete: true },
      accessOf({ run: false }),
    );
    expect(g).toMatchObject({
      canEdit: true,
      canRun: false,
      canDelete: false,
      canDownload: true,
    });
    expect(runTip(g)).toBe(reasons.run);
    expect(deleteTip(g)).toBe(reasons.run);
    expect(editTip(g)).toBe(NO_EDIT);
    expect(canRunOf(g)).toBe(false);
  });

  it("no download grant disables the CSV with its reason", () => {
    const g = leaderboardGates(
      { roleEdit: false, roleDelete: false },
      accessOf({ download: false, edit: false, run: false }),
    );
    expect(g.canDownload).toBe(false);
    expect(g.tips.download).toBe(reasons.download);
    // A viewer role keeps the role's own words.
    expect(g.tips.edit).toBe(NO_EDIT);
    expect(g.tips.delete).toBe(NO_DELETE);
  });

  it("before the access view is known the role decides (today's behaviour)", () => {
    const g = leaderboardGates({ roleEdit: true, roleDelete: true }, null);
    expect(g).toMatchObject({
      canEdit: true,
      canRun: true,
      canDelete: true,
      canDownload: true,
    });
    // An old context without canRun falls back to canEdit.
    expect(canRunOf({ canEdit: true })).toBe(true);
    expect(runTip({})).toBe(NO_EDIT);
  });
});

describe("headerAccessDoors", () => {
  it("Delete needs Manage: a member with Edit and run is refused with who may delete", () => {
    const d = headerAccessDoors({
      roleDelete: true,
      access: accessOf({ manage: false }),
    });
    expect(d.canDelete).toBe(false);
    expect(d.deleteReason).toBe(
      "Only the dataset's owner, workspace admins and people with Manage access can delete it. Ask Asha Rao.",
    );
    expect(d.canRun).toBe(true);
  });

  it("an admin, the owner or a Manage grant may delete; a viewer role may not", () => {
    expect(
      headerAccessDoors({
        roleDelete: true,
        access: accessOf({ manage: true }),
      }).canDelete,
    ).toBe(true);
    expect(headerAccessDoors({ roleDelete: false, access: null })).toEqual({
      canDelete: false,
      deleteReason: VIEWER_DELETE,
      canRun: true,
      requestDownload: null,
    });
    expect(
      headerAccessDoors({ roleDelete: true, access: accessOf({ run: false }) })
        .canRun,
    ).toBe(false);
  });

  it("a viewer without download is offered Request download access, then Requested", () => {
    const view = (requests = []) => ({
      can: { view: true, download: false, edit: false, run: false },
      reason: () => null,
      view: {
        me: {
          user_id: "u-me",
          perms: { view: true, download: false },
          requestable: ["download"],
        },
        requests,
        ask: [{ id: "a", name: "Asha Rao" }],
      },
    });
    expect(
      headerAccessDoors({ roleDelete: false, access: view() }).requestDownload,
    ).toEqual({ requested: false });
    expect(
      headerAccessDoors({
        roleDelete: false,
        access: view([{ perm: "download", user: { id: "u-me" } }]),
      }).requestDownload,
    ).toEqual({ requested: true });
    // At the viewer ceiling with a grant (nothing requestable): no door.
    const capped = view();
    capped.view.me.requestable = [];
    expect(
      headerAccessDoors({ roleDelete: false, access: capped }).requestDownload,
    ).toBeNull();
  });
});

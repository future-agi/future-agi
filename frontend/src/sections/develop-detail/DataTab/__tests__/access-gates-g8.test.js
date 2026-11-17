// v2.1 g8 (access:gates, proof walk #9): a member whose dataset access is View
// is blocked at the control (grid edit, Add rows / Add column, Run prompt).
import { describe, expect, it } from "vitest";
import { gridViewOnly, toolbarAccessGate } from "../dataAccessGate";

const VIEW = {
  view: true,
  download: true,
  edit: false,
  run: false,
  manage: false,
};
const EDIT_NO_RUN = {
  view: true,
  download: true,
  edit: true,
  run: false,
  manage: false,
};
const OPEN = {
  view: true,
  download: true,
  edit: true,
  run: true,
  manage: false,
};

describe("Data tab access gates", () => {
  it("View access makes the grid read-only even for a member role", () => {
    expect(gridViewOnly({ roleViewer: false, can: VIEW })).toBe(true);
    expect(gridViewOnly({ roleViewer: false, can: OPEN })).toBe(false);
    expect(gridViewOnly({ roleViewer: true, can: OPEN })).toBe(true);
    expect(gridViewOnly({ roleViewer: false })).toBe(false);
  });

  it("the toolbar names the server's reason when edit is off", () => {
    const g = toolbarAccessGate({
      can: VIEW,
      reason: (p) =>
        p === "edit" ? "Everyone in Support Agents has View" : null,
    });
    expect(g.editOff).toBe(true);
    expect(g.runOff).toBe(true);
    expect(g.editReason).toBe("Everyone in Support Agents has View");
    expect(g.runReason).toMatch(/Run/);
  });

  it("Edit without Run leaves writes on and turns runs off", () => {
    const g = toolbarAccessGate({ can: EDIT_NO_RUN, reason: () => null });
    expect(g.editOff).toBe(false);
    expect(g.editReason).toBe("");
    expect(g.runOff).toBe(true);
  });

  it("while access loads nothing is off", () => {
    const g = toolbarAccessGate({ can: OPEN });
    expect(g).toMatchObject({
      editOff: false,
      runOff: false,
      editReason: "",
      runReason: "",
    });
  });
});

// Access words (W4-5, A1): every permission × every via.
import { describe, expect, it } from "vitest";
import {
  denialLine,
  grantToast,
  levelFor,
  levelGranting,
  meLine,
  namesOr,
  nextMissingPerm,
  permPhrase,
  reasonFor,
  requestLabel,
  requestPerm,
  workspaceToast,
} from "../accessModel";

const LEVELS = [
  {
    key: "edit_run",
    label: "Edit and run",
    perms: ["view", "download", "edit", "run"],
  },
  { key: "edit", label: "Edit", perms: ["view", "download", "edit"] },
  {
    key: "view_download",
    label: "View and download",
    perms: ["view", "download"],
  },
  { key: "view", label: "View", perms: ["view"] },
  { key: "none", label: "No access", perms: [] },
];
const ASK = [
  { id: "a", name: "Asha Rao" },
  { id: "r", name: "Ravi Menon" },
];
const permsOf = (key) =>
  Object.fromEntries(
    ["view", "download", "edit", "run"].map((p) => [
      p,
      LEVELS.find((l) => l.key === key).perms.includes(p),
    ]),
  );
const viewOf = (level, via, workspace_level = "view") => ({
  workspace: { id: "w", name: "Support Agents" },
  workspace_level,
  levels: LEVELS,
  ask: ASK,
  me: { perms: { ...permsOf(level), manage: via === "admin" }, via, level },
});

describe("accessModel", () => {
  it("joins names and permissions the way people write them", () => {
    expect(namesOr([])).toBe("");
    expect(namesOr(ASK.slice(0, 1))).toBe("Asha Rao");
    expect(namesOr(ASK)).toBe("Asha Rao or Ravi Menon");
    expect(namesOr([...ASK, { name: "Meera Iyer" }])).toBe(
      "Asha Rao, Ravi Menon or Meera Iyer",
    );
    expect(permPhrase(permsOf("view"))).toBe("view");
    expect(permPhrase(permsOf("view_download"))).toBe("view and download");
    expect(permPhrase(permsOf("edit"))).toBe("view, download and edit");
    expect(nextMissingPerm(permsOf("view"))).toBe("download");
    expect(nextMissingPerm(permsOf("edit_run"))).toBeNull();
    expect(requestLabel("download")).toBe("Request download access");
  });

  it("finds the level a request needs and never lowers someone", () => {
    expect(levelFor("download", LEVELS).key).toBe("view_download");
    expect(levelFor("run", LEVELS).key).toBe("edit_run");
    expect(levelGranting("download", "view", LEVELS).key).toBe("view_download");
    expect(levelGranting("download", "edit", LEVELS).key).toBe("edit");
    expect(levelGranting("edit", undefined, LEVELS).key).toBe("edit");
  });

  it("the caller's own line", () => {
    expect(meLine(viewOf("view_download", "workspace"))).toBe(
      "You can view and download this dataset. Ask Asha Rao or Ravi Menon for more.",
    );
    expect(meLine(viewOf("view", "workspace"))).toBe(
      "You can view this dataset. Ask Asha Rao or Ravi Menon for more.",
    );
    expect(meLine(viewOf("none", "workspace", "none"))).toBe(
      "You don't have access to this dataset. Ask Asha Rao or Ravi Menon for more.",
    );
    expect(meLine(viewOf("edit_run", "admin"))).toBe(
      "You can view, download, edit and run this dataset.",
    );
  });

  it("a viewer at the ceiling is offered no request it can't get", () => {
    const atCeiling = viewOf("view_download", "ceiling");
    atCeiling.me.requestable = [];
    expect(requestPerm(atCeiling.me)).toBeNull();
    expect(meLine(atCeiling)).toBe(
      "You can view and download this dataset. Workspace viewers can't edit or run datasets.",
    );
    const viewer = viewOf("view", "ceiling");
    viewer.me.requestable = ["download"];
    expect(requestPerm(viewer.me)).toBe("download");
    // Without the server's list, the first missing permission.
    expect(requestPerm(viewOf("view", "workspace").me)).toBe("download");
  });

  it("a reason for every permission and every via", () => {
    const perms = ["view", "download", "edit", "run"];
    expect(perms.map((p) => reasonFor(p, viewOf("edit_run", "admin")))).toEqual(
      [null, null, null, null],
    );
    expect(reasonFor("manage", viewOf("edit_run", "workspace"))).toBe(
      "Only the dataset's owner, workspace admins and people with Manage access can change access. Ask Asha Rao or Ravi Menon.",
    );
    expect(reasonFor("download", viewOf("view", "workspace"))).toBe(
      "Everyone in Support Agents has View access to this dataset. You need download access. Ask Asha Rao or Ravi Menon.",
    );
    expect(reasonFor("run", viewOf("edit", "grant", "view"))).toBe(
      "You have Edit access to this dataset. You need run access. Ask Asha Rao or Ravi Menon.",
    );
    expect(reasonFor("download", viewOf("view", "ceiling"))).toBe(
      "Viewers can download only when an admin grants it. Ask Asha Rao or Ravi Menon.",
    );
    expect(reasonFor("edit", viewOf("view_download", "ceiling"))).toBe(
      "Workspace viewers can't edit datasets. Ask Asha Rao or Ravi Menon.",
    );
    expect(reasonFor("view", viewOf("none", "key_scope"))).toBe(
      "You need view access to this dataset. Ask Asha Rao or Ravi Menon.",
    );
    expect(reasonFor("view", null)).toBeNull();
  });

  it("denial bodies and toasts", () => {
    expect(
      denialLine({
        error: "You need run access to this dataset.",
        missing_permission: "run",
        ask: ASK,
      }),
    ).toBe("You need run access to this dataset. Ask Asha Rao or Ravi Menon.");
    expect(
      denialLine({
        error: "Viewers can't score review items. Ask a workspace member.",
        missing_permission: "score",
        ask: ASK,
      }),
    ).toBe("Viewers can't score review items. Ask a workspace member.");
    expect(denialLine({ error: "x" })).toBeNull();
    expect(grantToast("Alice Park", "view_download", LEVELS)).toBe(
      "Alice Park can now view and download",
    );
    expect(grantToast("Asha Rao", "edit_run", LEVELS)).toBe(
      "Asha Rao can now edit and run",
    );
    expect(grantToast("Asha Rao", null, LEVELS)).toBe(
      "Asha Rao no longer has their own access",
    );
    expect(workspaceToast("Support Agents", "view", LEVELS)).toBe(
      "Everyone in Support Agents can now view",
    );
    expect(workspaceToast("Support Agents", "none", LEVELS)).toBe(
      "Only the people listed and workspace admins can open this dataset now",
    );
  });
});

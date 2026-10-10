import { describe, expect, it } from "vitest";
import { savedViewStorageKeys } from "../savedViewStorage";

describe("identity-scoped saved view preferences", () => {
  it("isolates users and workspaces without reading legacy keys", () => {
    expect(savedViewStorageKeys("u", "w", "p")).toEqual({ display: "observe-display-u-w-p", filters: "observe-filters-u-w-p" });
    expect(savedViewStorageKeys("b", "w", "p").display).not.toBe(savedViewStorageKeys("u", "w", "p").display);
    expect(savedViewStorageKeys("u", "w2", "p").display).not.toBe(savedViewStorageKeys("u", "w", "p").display);
  });
  it.each([[null, "w"], ["u", null]])("has no storage keys before identity resolves", (user, workspace) => {
    expect(savedViewStorageKeys(user, workspace, "p")).toEqual({ display: null, filters: null });
  });
});

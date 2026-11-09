import { expect, it } from "vitest";
import { folderPickAfterMutation } from "./folderSelection";

it("keeps the current subtree selected after rename or move", () => {
  const pick = { kind: "folder", path: "support/billing" };
  expect(
    folderPickAfterMutation(pick, {
      action: "move",
      from: "support",
      to: "archive/support",
    }),
  ).toEqual({ kind: "folder", path: "archive/support/billing" });
  expect(
    folderPickAfterMutation(pick, {
      action: "rename",
      from: "support/billing",
      to: "support/refunds",
    }),
  ).toEqual({ kind: "folder", path: "support/refunds" });
});
it("shows retained datasets in No folder after removing their subtree", () => {
  expect(
    folderPickAfterMutation(
      { kind: "folder", path: "support/billing" },
      { action: "remove", from: "support" },
    ),
  ).toEqual({ kind: "none" });
});
it("does not change unrelated folder selections or filters", () => {
  for (const pick of [
    { kind: "folder", path: "support-other" },
    { kind: "starred" },
    { kind: "all" },
  ]) {
    expect(
      folderPickAfterMutation(pick, {
        action: "move",
        from: "support",
        to: "archive",
      }),
    ).toBe(pick);
    expect(folderPickAfterMutation(pick, { action: "create", to: "new" })).toBe(
      pick,
    );
  }
});

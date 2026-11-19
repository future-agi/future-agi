import { expect, it } from "vitest";
import { restoreCommands } from "./restoreCommands";

const commit = "a".repeat(40);

it("restores the lock of one reviewed commit in a single verified command", () => {
  const text = restoreCommands({ name: "support", version: 1, commit });
  expect(text.split("\n")[0]).toBe(
    `python -m fi.context restore --allow-preview --lock-from-git ${commit} --project . --apply --quiescent`,
  );
  expect(text).toContain("# Expect support v1.");
});

it("never places an unvalidated commit text into a shell command", () => {
  const text = restoreCommands({ name: "support", version: 1, commit: "main; rm -rf ." });
  expect(text).not.toContain("rm -rf");
  expect(text).toContain("<full id of the last reviewed commit>");
});

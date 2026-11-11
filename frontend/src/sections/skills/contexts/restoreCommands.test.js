import { expect, it } from "vitest";
import { restoreCommands } from "./restoreCommands";

it("restores from the retained lock with the explicit preview opt-in and quotes the path", () => {
  const text = restoreCommands({ name: "support", version: 1, lockPath: "it's a lock.json" });
  expect(text.split("\n")[0]).toBe(
    "python -m fi.context restore --allow-preview --lock 'it'\"'\"'s a lock.json' --project . --plan 'restore-plan.json'",
  );
  expect(text).toContain("fi.context apply --plan 'restore-plan.json' --quiescent");
  expect(text).toContain("# Expect support v1.");
});

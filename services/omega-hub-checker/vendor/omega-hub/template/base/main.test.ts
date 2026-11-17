// The keyless test `omega hub check` runs: the scripted model calls the tool, then answers from it.
import assert from "node:assert/strict";
import { test } from "node:test";
import { run } from "./main.ts";

test("the agent answers from the tool result, with no key", async () => {
  const result = await run();
  assert.equal(result.toolResults[0]?.name, "opening_hours");
  assert.match(String(result.content), /9:00-14:00/);
});

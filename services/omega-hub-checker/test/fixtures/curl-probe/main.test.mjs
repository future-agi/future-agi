import assert from "node:assert/strict";
import { test } from "node:test";
import { runProbes } from "./probes.mjs";

test("every network probe is stopped", async () => {
  const results = await runProbes();
  console.error(JSON.stringify(results));
  for (const result of results) assert.equal(result.reached, false, `${result.name} reached the network`);
});

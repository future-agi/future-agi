// The keyless test `omega hub check` runs: a killed run leaves two verified helpers on disk, the second run
// builds only the third, and the feature passes every check.
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { HELPERS, codeOf, crashAndResume } from "./main.ts";

test("after a SIGKILL the second run builds only the unfinished helper", async () => {
  const { dir, killed, verifiedBefore, second, modelCallsInRun2 } = await crashAndResume();
  assert.equal(killed.signal, "SIGKILL");
  assert.ok(verifiedBefore >= 2);
  assert.equal(second.result.verified, true);
  assert.equal(modelCallsInRun2, 1);
  for (const name of HELPERS) assert.ok(existsSync(join(dir, "repo", "src", `${name}.mjs`)), name);
  assert.ok(second.log.includes("CI readingTime: pass"));
  assert.ok(second.log.includes("CI all helpers together: pass"));
});

test("a live model\'s fenced or chatty reply becomes the file\'s code", () => {
  assert.equal(codeOf("```javascript\nexport const a = 1;\n```"), "export const a = 1;");
  assert.equal(codeOf("Here you go:\n```js\nexport const a = 1;\n```\nHope it helps."), "export const a = 1;");
  assert.equal(codeOf("export const a = 1;"), "export const a = 1;");
});

test("every helper goal states what its check expects", async () => {
  const { CONTRACT } = await import("./main.ts");
  assert.match(CONTRACT.readingTime, /plain number/);
  for (const name of HELPERS) assert.ok(CONTRACT[name].startsWith(`${name}(`));
});

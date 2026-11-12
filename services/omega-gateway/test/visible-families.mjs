import assert from "node:assert/strict";
import fs from "node:fs";
import { compileConsoleRouteManifest, MANIFEST_PATH } from "../src/manifest.mjs";

/**
 * Explicit positive-composition fixture for tests of a withdrawn family's behavior.
 * Only the named switches are removed from a fresh in-memory copy. Production
 * defaults and unrelated hidden families stay intact; absence tests use the
 * unmodified shipped manifest instead.
 */
export function visibleFamilyManifest(...families) {
  const source = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  for (const family of families) {
    assert.ok(Object.hasOwn(source.hidden, family), `Unknown hidden family: ${family}`);
    delete source.hidden[family];
  }
  return compileConsoleRouteManifest(source);
}

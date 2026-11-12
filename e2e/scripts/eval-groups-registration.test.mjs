import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// Registration coverage only: this fails if the groups flow is omitted from a
// composition. The Playwright flow, not this source check, proves UI/API/storage
// behavior. Keep FLOWS.md generation in the shared generated-only commit.
test("EVAL-E2E-013 registers the eval groups flow and its fallibility anchors", () => {
  const source = readFileSync(
    new URL("../flows/evals/eval-groups.spec.ts", import.meta.url),
    "utf8",
  );

  assert.match(source, /test\(\s*['"]EVAL-E2E-013:/);
  assert.match(source, /tag:\s*\[\s*['"]@flow['"]\s*\]/);
  assert.match(
    source,
    /annotation:\s*flowAnnotation\(\s*\{\s*id:\s*['"]EVAL-E2E-013['"],\s*area:\s*['"]evals['"]/,
  );
  for (const marker of [
    "GROUPS_NEG_MEMBERS",
    "GROUPS_NEG_TENANCY",
    "GROUPS_NEG_STORAGE",
  ]) {
    assert.ok(source.includes(marker), `missing fallibility anchor ${marker}`);
  }
});

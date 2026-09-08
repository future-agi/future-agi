import test from "node:test";
import assert from "node:assert/strict";
import { extractFlows, renderCatalog } from "./flow-catalog.mjs";

const entry = (id, area = "observe") => ({
  title: `${id}: does a thing`,
  location: `flows/${area}/x.spec.ts:10`,
  tags: ["@flow", "@smoke"],
  annotations: [
    {
      type: "flow",
      description: JSON.stringify({
        id,
        area,
        userGoal: "goal",
        steps: ["a"],
        backendChecks: ["b"],
      }),
    },
  ],
});

test("extracts well-formed flows", () => {
  const flows = extractFlows([entry("OBS-E2E-001")]);
  assert.equal(flows.length, 1);
  assert.equal(flows[0].id, "OBS-E2E-001");
});

test("rejects duplicate ids", () => {
  assert.throws(
    () => extractFlows([entry("OBS-E2E-001"), entry("OBS-E2E-001")]),
    /duplicate/i,
  );
});

test("rejects @flow tests without annotation", () => {
  assert.throws(
    () => extractFlows([{ ...entry("X"), annotations: [] }]),
    /missing flow annotation/i,
  );
});

test("rejects empty flow sets", () => {
  assert.throws(() => extractFlows([]), /no @flow tests/i);
});

test("rejects an annotation missing a required field", () => {
  const { steps, ...rest } = JSON.parse(
    entry("OBS-E2E-001").annotations[0].description,
  );
  const bad = {
    ...entry("OBS-E2E-001"),
    annotations: [{ type: "flow", description: JSON.stringify(rest) }],
  };
  assert.throws(() => extractFlows([bad]), /annotation missing steps/);
});

test("sorts areas alphabetically and flows by id within an area", () => {
  const md = renderCatalog(
    extractFlows([
      entry("PROJ-E2E-002", "projects"),
      entry("OBS-E2E-002"),
      entry("PROJ-E2E-001", "projects"),
      entry("OBS-E2E-001"),
    ]),
  );
  assert.deepEqual(
    md.split("\n").filter((l) => l.startsWith("## ") || l.startsWith("### ")),
    [
      "## observe",
      "### OBS-E2E-001 — does a thing",
      "### OBS-E2E-002 — does a thing",
      "## projects",
      "### PROJ-E2E-001 — does a thing",
      "### PROJ-E2E-002 — does a thing",
    ],
  );
});

test("renders grouped markdown", () => {
  const md = renderCatalog(
    extractFlows([entry("OBS-E2E-001"), entry("PROJ-E2E-001", "projects")]),
  );
  assert.match(md, /## observe/);
  assert.match(md, /## projects/);
  assert.match(md, /OBS-E2E-001/);
});

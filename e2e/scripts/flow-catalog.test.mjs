import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, writeFileSync } from "node:fs";
import path from "node:path";
import {
  collectFlowEntries,
  extractFlows,
  renderCatalog,
} from "./flow-catalog.mjs";

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

test("collection failure rejects even when the reporter wrote valid partial entries", () => {
  let output;
  const failure = new Error("a different spec failed to import");
  assert.throws(
    () =>
      collectFlowEntries((_command, _args, options) => {
        output = options.env.FLOW_CATALOG_OUT;
        writeFileSync(output, JSON.stringify([entry("OBS-E2E-001")]));
        throw failure;
      }),
    (error) => error === failure,
  );
  assert.equal(existsSync(path.dirname(output)), false);
});

test("successful collection includes gated flows and removes its private temporary directory", () => {
  let output;
  const entries = [entry("OBS-E2E-001")];
  assert.deepEqual(
    collectFlowEntries((command, args, options) => {
      assert.equal(command, "npx");
      assert.ok(args.includes("--list"));
      assert.equal(options.env.E2E_INCLUDE_QUARANTINED, "1");
      assert.equal(options.env.E2E_LIVE_LLM, "1");
      output = options.env.FLOW_CATALOG_OUT;
      writeFileSync(output, JSON.stringify(entries));
    }),
    entries,
  );
  assert.equal(existsSync(path.dirname(output)), false);
});

for (const content of [undefined, "invalid json"]) {
  test(`missing or malformed reporter output fails closed (${content})`, () => {
    let output;
    assert.throws(() =>
      collectFlowEntries((_command, _args, options) => {
        output = options.env.FLOW_CATALOG_OUT;
        if (content !== undefined) writeFileSync(output, content);
      }),
    );
    assert.equal(existsSync(path.dirname(output)), false);
  });
}

for (const [field, values] of Object.entries({
  id: [42, {}, [], " "],
  area: [42, {}, [], " "],
  userGoal: [42, {}, [], " "],
  steps: ["step", {}, [null], [" "]],
  backendChecks: ["check", {}, [null], [" "]],
  browserChecks: ["check", {}, [null], [" "]],
})) {
  test(`rejects malformed ${field} before rendering the catalog`, () => {
    for (const value of values) {
      const bad = entry("OBS-E2E-001");
      const meta = JSON.parse(bad.annotations[0].description);
      bad.annotations[0].description = JSON.stringify({
        ...meta,
        [field]: value,
      });
      assert.throws(
        () => extractFlows([bad]),
        new RegExp(`annotation .*${field}`),
      );
    }
  });
}

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

test("labels browser-only checks without claiming backend verification", () => {
  const browserEntry = entry("FEED-E2E-001", "error-feed");
  const meta = JSON.parse(browserEntry.annotations[0].description);
  delete meta.backendChecks;
  browserEntry.annotations[0].description = JSON.stringify({
    ...meta,
    browserChecks: ["redirect request receives a stubbed response"],
  });
  const md = renderCatalog(extractFlows([browserEntry]));
  assert.match(md, /Browser observations:/);
  assert.doesNotMatch(md, /Backend state verified:/);
});

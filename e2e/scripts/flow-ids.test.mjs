import test from "node:test";
import assert from "node:assert/strict";
import { flowIdsAndAreas } from "./flow-ids.mjs";

test("reads flow ids and areas from annotation literals", () => {
  const text = `test('OBS-E2E-001: x', { annotation: flowAnnotation({ id: 'OBS-E2E-001', area: "observe" }) });`;
  assert.deepEqual(flowIdsAndAreas([text]), {
    ids: ["OBS-E2E-001"],
    areas: ["observe"],
  });
});

test("reads ids from a table that builds several flows", () => {
  const text = `for (const [entry, id] of [['dataset', 'DATA-E2E-003'], ['experiment', 'DATA-E2E-004']] as const) {}`;
  assert.deepEqual(flowIdsAndAreas([text]).ids, [
    "DATA-E2E-003",
    "DATA-E2E-004",
  ]);
});

test("ignores an id mentioned in prose or a comment", () => {
  assert.deepEqual(
    flowIdsAndAreas(["// same setup as OBS-E2E-007, see its spec"]).ids,
    [],
  );
});

// The adopted Console union needs G04's dedicated credential class; it must not
// be mistaken for an ordinary host assertion or accepted on an unrelated route.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { compileConsoleRouteManifest, MANIFEST_PATH } from "../src/manifest.mjs";

const raw = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
const eventPath = "/workspace/triggers/{id}/events";

test("the union compiles signed trigger events with their dedicated credential", () => {
  const manifest = compileConsoleRouteManifest(raw);
  const event = manifest.routes.find(
    (route) => route.method === "POST" && route.path === eventPath,
  );
  assert.ok(event);
  assert.equal(event.credential, "trigger-signature");
  const misspelled = structuredClone(raw);
  misspelled.routes.find(
    (route) => route.method === "POST" && route.path === eventPath,
  ).credential = "trigger-signature-typo";
  assert.throws(
    () => compileConsoleRouteManifest(misspelled),
    /unknown credential class/,
  );
});

test("the signed-trigger credential cannot authorize another union route", () => {
  const widened = structuredClone(raw);
  const other = widened.routes.find(
    (route) => route.method === "GET" && route.path === "/workspace/capabilities",
  );
  assert.ok(other);
  other.credential = "trigger-signature";
  assert.throws(
    () => compileConsoleRouteManifest(widened),
    /cannot use trigger signatures/,
  );
});

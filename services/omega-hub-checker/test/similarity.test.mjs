import assert from "node:assert/strict";
import { test } from "node:test";
import { similarityFindings } from "../src/similarity.mjs";
import { fixture, sha } from "./helpers.mjs";

const codeHashes = (files) => [...files].filter(([rel]) => /\.(m?js|ts|tsx|html)$/.test(rel)).map(([, data]) => sha(data));

test("copy-of-good gets undeclared-copy", () => {
  const found = similarityFindings(fixture("copy-of-good"), { codeHashes: { "acme/good-agent": codeHashes(fixture("good")) }, ownRef: "acme/weather-copy" });
  assert.deepEqual(found.map((item) => item.rule), ["undeclared-copy"]);
  assert.match(found[0].message, /identical to acme\/good-agent/);
});

test("naming the origin in remix_of, or sharing less than half, is not flagged", () => {
  const hashes = { "acme/good-agent": codeHashes(fixture("good")) };
  assert.deepEqual(similarityFindings(fixture("copy-of-good"), { codeHashes: hashes, remixOf: "acme/good-agent@1.0.0" }), []);
  assert.deepEqual(similarityFindings(fixture("curl-probe"), { codeHashes: hashes }), []);
  assert.deepEqual(similarityFindings(fixture("good"), { codeHashes: hashes, ownRef: "acme/good-agent" }), []);
});

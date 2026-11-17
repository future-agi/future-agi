import assert from "node:assert/strict";
import test from "node:test";
import { detect } from "./evidence-audit/detect.mjs";

// lib/provider-key.ts mirrors Django's provider model filter
// (futureagi/agentcc/services/provider_model_safety.py): a provider saved with a
// key loses every model id that contains a window of that key.
const { keyHidesModel, syntheticProviderKey } = await import(
  "../lib/provider-key.ts"
);

const RUN = "0-mh3k2x1a"; // `${workerIndex}-${Date.now().toString(36)}`

test("keyHidesModel follows the window rule: 8-character text, case-folded and compacted, short keys only whole", () => {
  // The seeds the flows used: the run suffix alone is a shared window.
  assert.equal(keyHidesModel(`sk-e2e-gw26-${RUN}`, `e2e-gw26-m-${RUN}`), true);
  assert.equal(keyHidesModel(`sk-e2e-other-${RUN}`, `model-${RUN}`), true);
  assert.equal(keyHidesModel("sk-e2e-gw26-x", "E2E-GW26-other"), true);
  // Compacted: separators inserted between the same eight characters.
  assert.equal(keyHidesModel("key-abcd-efgh-z", "abcd.efgh-model"), true);
  // Seven shared characters are not a window.
  assert.equal(keyHidesModel("sk1234567x", "m1234567y"), false);
  // A key shorter than its window hides only an equal id (Ollama's "x").
  assert.equal(keyHidesModel("x", "mixtral-8x7b"), false);
  assert.equal(keyHidesModel("EMPTY", "empty"), true);
  // Hex (16) and base64 (12) forms are windows too.
  const key = "sk-e2e-synthetic-hexform";
  const hex = Buffer.from(key).toString("hex");
  assert.equal(keyHidesModel(key, `m-${hex.slice(4, 20)}`), true);
  const b64 = Buffer.from(key).toString("base64").replace(/=+$/, "");
  assert.equal(keyHidesModel(key, `m-${b64.slice(3, 15)}`), true);
});

test("syntheticProviderKey keeps every model id the flows mint, and differs per call", () => {
  const flows = [
    [`e2e-gw26-m-${RUN}`],
    [`e2e-gw15-m1-${RUN}`, `e2e-gw15-m2-${RUN}`],
    [`e2e-gw17-model-${RUN}`],
    [`e2e-gw16-model-${RUN}`, `e2e-gw16-mc-${RUN}`],
    [1, 2, 3].map((i) => `e2e-gw11-m${i}-${RUN}`),
    [`e2e-model-${RUN}`, `e2e-manual-${RUN}`],
    [`w18-model-walks-${RUN}`],
    [`e2e-w01-byok-model-walks-${RUN}`],
    [`e2e-h2-model-h2-mh3k2x1a-k3x9qa`],
  ];
  const keys = new Set();
  for (const models of flows) {
    const key = syntheticProviderKey(models);
    assert.match(key, /^sk-e2e-synthetic-[0-9a-f]{24}$/);
    assert.deepEqual(
      models.filter((model) => keyHidesModel(key, model)),
      [],
    );
    keys.add(key);
  }
  assert.equal(keys.size, flows.length);
});

test("syntheticProviderKey refuses rather than return a key that hides a model", () => {
  assert.throws(
    () => syntheticProviderKey(["mirror-sk-e2e-synthetic-model"]),
    /no synthetic provider key keeps all of 1 model id/,
  );
});

test("the evidence audit does not flag a synthetic provider key", () => {
  for (let i = 0; i < 20; i++) {
    const key = syntheticProviderKey(["gpt-4o-mini"]);
    assert.deepEqual(detect(JSON.stringify({ note: key })), []);
  }
});

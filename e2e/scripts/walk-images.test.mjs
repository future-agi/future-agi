import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { attestImages } from "./walk-images.mjs";
const image = {
  tag: "futureagi/frontend:walk-head",
  id: `sha256:${"a".repeat(64)}`,
};
const running = () => [
  {
    Image: image.id,
    State: { Running: true },
    Config: {
      Image: image.tag,
      Labels: { "com.docker.compose.service": "frontend" },
    },
  },
];
test("image attestation records the local content digest only for the built tag", () => {
  assert.deepEqual(attestImages({ frontend: image }, running()), [
    { service: "frontend", tag: image.tag, imageId: image.id },
  ]);
});
for (const wrong of ["registry", "stale", "stopped", "missing", "ambiguous"]) {
  test(`image attestation refuses ${wrong}`, () => {
    const r = running();
    if (wrong === "registry") r[0].Config.Image = "futureagi/frontend:latest";
    if (wrong === "stale") r[0].Image = `sha256:${"b".repeat(64)}`;
    if (wrong === "stopped") r[0].State.Running = false;
    if (wrong === "missing") r.pop();
    if (wrong === "ambiguous") r.push(r[0]);
    assert.throws(() => attestImages({ frontend: image }, r));
  });
}
// bin/e2e refuses raw Compose rendering (`compose config` / `convert`), because it would
// expand the runtime credentials. The running check must not ask for it: when it did,
// every walk stopped at IMAGE-MISMATCH right after a successful build.
test("the running check never asks bin/e2e for rendered Compose config", () => {
  const src = readFileSync(new URL("./walk-images.mjs", import.meta.url), "utf8");
  for (const word of ['"config"', '"convert"', "'config'", "'convert'"]) {
    assert.ok(!src.includes(word), `walk-images.mjs passes ${word} to bin/e2e`);
  }
});

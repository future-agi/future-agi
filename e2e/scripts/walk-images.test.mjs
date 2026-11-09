import { test } from "node:test";
import assert from "node:assert/strict";
import { attestImages } from "./walk-images.mjs";
const image = {
  tag: "futureagi/frontend:walk-head",
  id: `sha256:${"a".repeat(64)}`,
};
const config = { frontend: { image: image.tag } };
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
  assert.deepEqual(attestImages({ frontend: image }, config, running()), [
    { service: "frontend", tag: image.tag, imageId: image.id },
  ]);
});
for (const wrong of ["registry", "stale", "stopped", "missing", "ambiguous"]) {
  test(`image attestation refuses ${wrong}`, () => {
    const c = structuredClone(config);
    const r = running();
    if (wrong === "registry") c.frontend.image = "futureagi/frontend:latest";
    if (wrong === "stale") r[0].Image = `sha256:${"b".repeat(64)}`;
    if (wrong === "stopped") r[0].State.Running = false;
    if (wrong === "missing") r.pop();
    if (wrong === "ambiguous") r.push(r[0]);
    assert.throws(() => attestImages({ frontend: image }, c, r));
  });
}

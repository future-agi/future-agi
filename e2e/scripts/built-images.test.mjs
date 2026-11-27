import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";
import { parse } from "yaml";
import { builtRefs, record, verify } from "./built-images.mjs";

const root = new URL("../../", import.meta.url);
const job = parse(
  readFileSync(new URL(".github/workflows/e2e-ci.yml", root), "utf8"),
).jobs.e2e;
const at = (find) => {
  const index = job.steps.findIndex(find);
  assert.notEqual(index, -1, "step not found");
  return index;
};
const named = (name) => at((s) => s.name === name);

test("one line picks the builder of the stack images: Docker's own", () => {
  // Docker's own builder writes each image straight into the store the stack
  // runs from. The buildx container builder packed every image into an
  // archive, loaded it, and left a second copy that a later step deleted.
  assert.equal(job.env.BUILDX_BUILDER, "default");
  // Emptying that one line brings the container builder back: its setup and
  // its clean-up both follow the line, and no command names a builder.
  const followsTheLine = "env.BUILDX_BUILDER != 'default'";
  const setup = job.steps.find((s) =>
    s.uses?.startsWith("docker/setup-buildx-action@"),
  );
  assert.equal(setup.if, followsTheLine);
  const prune = job.steps.filter((s) => s.run?.includes("buildx prune"));
  assert.equal(prune.length, 1);
  assert.equal(prune[0].if, followsTheLine);
  for (const step of job.steps) {
    assert.doesNotMatch(step.run ?? "", /--builder\b/, step.name);
    // The Standalone assemble reads the images built before it from the local
    // store, which only Docker's own builder can do, whatever the line says.
    assert.ok(
      [undefined, "default"].includes(step.env?.BUILDX_BUILDER),
      step.name,
    );
  }
});

test("free disk is printed after the builds on every leg, whichever builder ran", () => {
  const disk = at((s) => /^df -h \/ \/mnt$/m.test(s.run ?? "") && !s.if);
  assert.ok(disk > named("Assemble futureagi/standalone from those images"));
  assert.ok(disk < named("Boot the e2e stack"));
});

test("the leg records the images it built before the boot and checks the stack ran them", () => {
  const recorded = at((s) => /built-images\.mjs record /.test(s.run ?? ""));
  const checked = at((s) => /built-images\.mjs verify /.test(s.run ?? ""));
  const lastBuild = Math.max(
    ...job.steps.flatMap((s, i) =>
      /docker build|buildx bake|bin\/e2e build/.test(s.run ?? "") ? [i] : [],
    ),
  );
  assert.ok(lastBuild < recorded && recorded < named("Boot the e2e stack"));
  assert.equal(job.steps[recorded].if, undefined);
  // The check follows the suite and its diagnostics, whose order other tests
  // pin, and runs on every leg whose stack booted, whatever the suite did.
  assert.ok(checked > named("Upload Redis diagnostics"));
  assert.equal(
    job.steps[checked].if,
    "${{ !cancelled() && steps.boot.outcome == 'success' }}",
  );
  assert.equal(job.steps[checked]["continue-on-error"], undefined);
  const file = (step) => step.run.match(/built-images\.mjs \w+ (\S+)/)[1];
  assert.equal(file(job.steps[recorded]), file(job.steps[checked]));
});

const env = {
  IMAGE_TAG: "e2e-ci",
  E2E_STACK: "distributed",
  BUILD_BACKEND: "true",
  BUILD_FRONTEND: "true",
  BUILD_GATEWAY: "true",
};

test("the built images are the ones the build steps tag", () => {
  assert.deepEqual(builtRefs(env), [
    "futureagi/code-executor:e2e-ci",
    "futureagi/future-agi:e2e-ci",
    "futureagi/fi-collector:e2e-ci",
    "futureagi/frontend:e2e-ci",
    "futureagi/agentcc-gateway:e2e-ci",
  ]);
  assert.deepEqual(
    builtRefs({ ...env, IMAGE_TAG: "e2esa", E2E_STACK: "standalone" }).at(-1),
    "futureagi/standalone:e2esa",
  );
  assert.deepEqual(
    builtRefs({ IMAGE_TAG: "e2e-ci", E2E_STACK: "distributed" }),
    ["futureagi/code-executor:e2e-ci"],
  );
  // The job's "Record the stack's pulled images" step marks the same list.
  const marked = job.steps[named("Record the stack's pulled images")].run;
  for (const ref of builtRefs({ ...env, E2E_STACK: "standalone" })) {
    assert.ok(marked.includes(ref.replace(":e2e-ci", ":$IMAGE_TAG")), ref);
  }
  assert.throws(() => builtRefs({ ...env, IMAGE_TAG: "" }), /IMAGE_TAG/);
});

test("recording fails when a built image is not in the local store", () => {
  const store = { "futureagi/code-executor:e2e-ci": "sha256:aa" };
  const inspect = (ref) => store[ref];
  assert.deepEqual(record(["futureagi/code-executor:e2e-ci"], inspect), store);
  assert.throws(
    () =>
      record(
        [
          "futureagi/code-executor:e2e-ci",
          "futureagi/frontend:e2e-ci",
          "futureagi/future-agi:e2e-ci",
        ],
        inspect,
      ),
    /not in the local Docker store: futureagi\/frontend:e2e-ci, futureagi\/future-agi:e2e-ci/,
  );
});

test("the check fails when a container runs another image under a built tag", () => {
  const built = {
    "futureagi/future-agi:e2e-ci": "sha256:aa",
    "futureagi/frontend:e2e-ci": "sha256:bb",
  };
  const backend = { ref: "futureagi/future-agi:e2e-ci", id: "sha256:aa" };
  const vendor = { ref: "postgres:16", id: "sha256:cc" };
  assert.deepEqual(verify(built, [backend, backend, vendor]), [
    "futureagi/future-agi:e2e-ci",
  ]);
  // Pulled or rebuilt after the record: same tag, another image.
  assert.throws(
    () => verify(built, [backend, { ...backend, id: "sha256:dd" }, vendor]),
    /futureagi\/future-agi:e2e-ci runs sha256:dd, this job built sha256:aa/,
  );
  // A stack that runs none of the built images proves nothing.
  assert.throws(() => verify(built, [vendor]), /none of the images/);
  assert.throws(() => verify(built, []), /none of the images/);
});

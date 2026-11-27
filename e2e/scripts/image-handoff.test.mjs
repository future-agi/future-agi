import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";

const root = new URL("../../", import.meta.url);
const workflow = (name) =>
  parse(readFileSync(new URL(`.github/workflows/${name}.yml`, root), "utf8"));

test("image handoff verifier accepts matching bytes and rejects broken bindings", () => {
  execFileSync(
    "python3",
    [
      "-I",
      "-B",
      fileURLToPath(new URL(".github/scripts/test_image_handoff.py", root)),
    ],
    { stdio: "inherit" },
  );
});

test("proof remains dispatch-only and uses the distributed backend recipe", () => {
  const proof = workflow("e2e-image-handoff-proof");
  // Dispatch runs the proof; the dev push only registers it, every job skipped.
  assert.deepEqual(Object.keys(proof.on).sort(), ["push", "workflow_dispatch"]);
  assert.deepEqual(proof.on.push, {
    branches: ["dev"],
    paths: [".github/workflows/e2e-image-handoff-proof.yml"],
  });
  assert.equal(proof.jobs.donor.if, "github.event_name == 'workflow_dispatch'");
  assert.deepEqual(Object.keys(proof.jobs).sort(), ["consumer", "donor"]);
  assert.equal(proof.jobs.consumer.needs, "donor");
  assert.equal(proof.jobs.consumer.if, undefined); // skipped with the donor
  const ordinary = workflow("e2e-ci");
  const donor = proof.jobs.donor.steps.find(
    (s) => s.with?.file === "futureagi/Dockerfile.oss",
  ).with;
  // Ordinary CI builds the backend from e2e/stack/docker-bake.hcl: its
  // Distributed recipe, as buildx itself resolves it.
  const reference = JSON.parse(
    execFileSync(
      "docker",
      [
        "buildx",
        "bake",
        "-f",
        "e2e/stack/docker-bake.hcl",
        "--print",
        "backend",
      ],
      {
        cwd: fileURLToPath(root),
        encoding: "utf8",
        env: {
          ...process.env,
          E2E_STACK: "distributed",
          IMAGE_TAG: "e2e-ci",
          CACHE_REPO: ordinary.env.CACHE_REPO,
        },
      },
    ),
  ).target.backend;
  assert.equal(donor.context, reference.context);
  assert.equal(donor.file, `${reference.context}/${reference.dockerfile}`);
  assert.equal(donor.load, true);
  assert.deepEqual(reference.output, [{ type: "docker" }]);
  assert.equal(donor.target, reference.target);
  assert.equal(donor["build-args"] ?? "", "", "Distributed build arguments");
  assert.equal(reference.args, undefined); // standard variant is the Dockerfile default
  // Without a platform, ordinary CI builds for its hosted runner's architecture.
  assert.equal(reference.platforms, undefined);
  assert.equal(ordinary.jobs.e2e["runs-on"], "ubuntu-latest");
  assert.equal(donor.platforms, "linux/amd64");
  assert.equal(donor.tags, "futureagi/future-agi:e2e-ci");
  assert.deepEqual(reference.tags, [donor.tags]);
  assert.equal(
    donor["cache-from"],
    `type=registry,ref=${ordinary.env.CACHE_REPO}:backend`,
  );
  assert.deepEqual(reference["cache-from"], [
    { type: "registry", ref: `${ordinary.env.CACHE_REPO}:backend` },
  ]);
  assert.equal(donor["cache-to"], undefined);
  assert.equal(donor.push, false);
  const overlay = (steps) =>
    steps
      .find((s) => s.run?.includes("-f e2e/stack/backend/Dockerfile"))
      .run.replaceAll('"', "")
      .replaceAll("$IMAGE_TAG", "e2e-ci")
      .replace("--platform linux/amd64", "")
      .replaceAll("\\\n", " ")
      .replace(/\s+/g, " ")
      .trim();
  assert.equal(
    overlay(proof.jobs.donor.steps),
    overlay(ordinary.jobs.e2e.steps),
  );
});

test("E2E CI builds each stack its five images from one bake file, loaded locally", () => {
  const step = workflow("e2e-ci").jobs.e2e.steps.find(
    (s) => s.name === "Build the stack images from PR code",
  );
  assert.match(
    step.run,
    /docker buildx bake -f e2e\/stack\/docker-bake\.hcl "\$\{targets\[@\]\}"/,
  );
  const plan = (stack) =>
    JSON.parse(
      execFileSync(
        "docker",
        ["buildx", "bake", "-f", "e2e/stack/docker-bake.hcl", "--print"],
        {
          cwd: fileURLToPath(root),
          encoding: "utf8",
          env: { ...process.env, E2E_STACK: stack, IMAGE_TAG: "t" },
        },
      ),
    ).target;
  for (const stack of ["distributed", "standalone"]) {
    const targets = plan(stack);
    assert.deepEqual(Object.keys(targets).sort(), [
      "backend",
      "code-executor",
      "fi-collector",
      "frontend",
      "gateway",
    ]);
    for (const [name, target] of Object.entries(targets)) {
      assert.deepEqual(target.output, [{ type: "docker" }], name);
      assert.equal(target["cache-to"], undefined, name); // read only; e2e-cache.yml writes
      assert.equal(target.tags.length, 1, name);
      assert.match(target.tags[0], /^futureagi\/[a-z-]+:t$/, name);
    }
    const backend = targets.backend;
    if (stack === "standalone") {
      assert.deepEqual(backend.args, { IMAGE_VARIANT: "slim" });
      assert.equal(backend["cache-from"].length, 3);
    } else {
      assert.equal(backend.args, undefined);
      assert.equal(backend["cache-from"].length, 1);
    }
  }
});

// The Skills cohort jobs in e2e-ci.yml build and boot exactly as the ordinary
// legs do. actionlint 1.7.7 predates YAML aliases, so they carry explicit copies
// of the shared steps; this keeps the copies identical to the ordinary leg's.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { parse } from "yaml";

const workflow = parse(readFileSync(new URL("../../.github/workflows/e2e-ci.yml", import.meta.url), "utf8"));
const jobs = workflow.jobs;
const key = (step) => step.name ?? step.uses;

const SHARED = [
  "actions/checkout@v4",
  "Reclaim runner disk",
  "docker/setup-buildx-action@v3",
  "ghcr login for read-only cache imports",
  "Build backend image from PR code",
  "Add E2E-only fixtures and telemetry isolation to backend image",
  "Build code-executor image from PR code",
  "Select the source-built executor in both stacks",
  "Build fi-collector image from PR code",
  "Add local editor assets to the E2E frontend",
  "Build agentcc-gateway image from PR code",
  "Assemble futureagi/standalone from those images",
  "Drop the buildkit copy of the built images",
  "Point the stack at the built images",
  "Prepare public host status directory",
  "Prefetch vendor images",
  "actions/setup-node@v4",
  "Install harness",
  "Restore Playwright browser cache",
  "Install Chromium",
];

test("both cohort jobs run the same steps", () => {
  assert.deepEqual(jobs["campaign-executor"].steps, jobs.skills.steps);
});

test("every shared step is a byte-for-byte copy of the ordinary leg's", () => {
  const ordinary = jobs.e2e.steps;
  for (const name of SHARED) {
    const expected = ordinary.filter((step) => key(step) === name);
    const actual = jobs.skills.steps.filter((step) => key(step) === name);
    assert.equal(expected.length, 1, `ordinary leg: ${name}`);
    assert.equal(actual.length, 1, `cohort job: ${name}`);
    assert.deepEqual(actual[0], expected[0], name);
  }
  // Same relative order as the ordinary leg.
  const order = (steps) => steps.map(key).filter((name) => SHARED.includes(name));
  assert.deepEqual(order(jobs.skills.steps), order(jobs.e2e.steps).filter((name, i, all) => all.indexOf(name) === i));
});

test("cohort-only and ordinary-only steps stay where they belong", () => {
  const cohort = jobs.skills.steps.map(key);
  for (const name of ["Verify package-producing job completed", "Build frontend image with the Skills trace UI",
    "Prepare verified Skills clients before stack boot", "Run the Skills cohort", "Upload Skills execution receipt"]) {
    assert.ok(cohort.includes(name), name);
  }
  const frontend = jobs.skills.steps.find((step) => step.name === "Build frontend image with the Skills trace UI");
  assert.equal(frontend.with["build-args"], "VITE_SKILL_TRACES=on");
  // The ordinary leg keeps the production default (frontend/Dockerfile: ARG VITE_SKILL_TRACES=off).
  assert.equal(jobs.e2e.steps.find((step) => step.name === "Build frontend image from PR code").with["build-args"], undefined);
  for (const name of ["Install the local SAML IdP's dependencies", "Start Redis diagnostics", "Upload blob report",
    "Resolve the futureagi-sdk ref", "Record the once-per-stack steps", "Dump stack state on failure"]) {
    assert.ok(!cohort.includes(name), name);
  }
  assert.equal(jobs.e2e.env.SKILLS_COHORT, "ordinary");
  assert.equal(jobs.report.env.SKILLS_COHORT, "ordinary");
  assert.equal(jobs.skills.env.SKILLS_COHORT, "${{ matrix.cohort }}");
});

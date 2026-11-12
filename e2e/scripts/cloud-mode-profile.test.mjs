import assert from "node:assert/strict";
import { test } from "node:test";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  symlinkSync,
  rmSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  CLOUD_MODE_ACCEPTED,
  CLOUD_MODE_VARIABLES,
  parseCloudModeProfile,
  loadCloudModeProfile,
} from "./cloud-mode-profile.mjs";
const fields = {
  E2E_PROFILE: "cloud-mode",
  E2E_STACK: "standalone",
  E2E_COMPOSE_PROJECT: "futureagi-e2e-cloudmode-attempt-a",
  E2E_REDIS_URL: "redis://localhost:26379/0",
  E2E_STRIPE_SHIM_URL: "http://localhost:12112",
};

test("cloud-mode remains explicitly not accepted", () =>
  assert.equal(CLOUD_MODE_ACCEPTED, false));
test("only contract 713 variables are consumed; each attempt keeps its own project", () => {
  for (const suffix of ["a", "b"]) {
    const f = {
      ...fields,
      E2E_COMPOSE_PROJECT: `futureagi-e2e-cloudmode-attempt-${suffix}`,
      PRIVATE_SIGNING_FILE: "/never/open",
    };
    const env = parseCloudModeProfile(JSON.stringify(f), "json");
    assert.equal(env.E2E_COMPOSE_PROJECT, f.E2E_COMPOSE_PROJECT);
    assert.ok(Object.keys(env).every((k) => CLOUD_MODE_VARIABLES.includes(k)));
    assert.equal(env.PRIVATE_SIGNING_FILE, undefined);
    assert.deepEqual(
      parseCloudModeProfile(
        Object.entries(f)
          .map(([k, v]) => `${k}=${v}`)
          .join("\n"),
        "env",
      ),
      env,
    );
  }
});
for (const delta of [
  { E2E_COMPOSE_PROJECT: "" },
  { E2E_APP_URL: "https://outside.invalid" },
  { E2E_PROFILE: "stock" },
  { E2E_COMPOSE_PROJECT: "$(whoami)" },
  { E2E_STACK: "distributed" },
]) {
  test(`cloud-mode refuses invalid ${Object.keys(delta)[0]}`, () =>
    assert.throws(() =>
      parseCloudModeProfile(JSON.stringify({ ...fields, ...delta }), "json"),
    ));
}
test("loader reads only the chosen profile file and refuses absent overlays and symlink substitutes", () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "cloud-profile-"));
  const root = path.join(dir, "checkout"),
    overlay = path.join(dir, "overlay");
  mkdirSync(root);
  mkdirSync(overlay);
  try {
    // A poison signing path proves no fallback/scan opens it.
    mkdirSync(path.join(overlay, "signing.key"));
    writeFileSync(
      path.join(overlay, "profile.env"),
      Object.entries(fields)
        .map(([k, v]) => `${k}=${v}`)
        .join("\n"),
    );
    assert.equal(
      loadCloudModeProfile(overlay, root).E2E_COMPOSE_PROJECT,
      fields.E2E_COMPOSE_PROJECT,
    );
    assert.throws(
      () => loadCloudModeProfile(undefined, root),
      /E2E_CLOUD_MODE_OVERLAY/,
    );
    assert.throws(() => loadCloudModeProfile(root, root), /outside/);
    symlinkSync(
      path.join(overlay, "signing.key"),
      path.join(overlay, "profile.json"),
    );
    assert.throws(
      () => loadCloudModeProfile(overlay, root, "json"),
      /non-symlink/,
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

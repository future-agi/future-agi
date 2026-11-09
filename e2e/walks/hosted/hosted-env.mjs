#!/usr/bin/env node
// H14: the synthetic environment the hosted walk (W19) runs deploy/
// docker-compose.hosted.yml with, rendered as G18's compose test renders it
// (deploy/tests/test_hosted_deployment.py on gw/G18-HOSTED-PREP-1): every
// `${NAME:?...}` the hosted file requires gets a fresh random value, except the
// ones that must have a shape (a Fernet key, the stand-in hosts of
// docker-compose.hosted-walk.yml, the local images by digest, the walk's own
// loopback URLs). Nothing here is a real credential, and nothing is kept: the
// file is written 0600 outside the checkout, read by `docker compose
// --env-file`, and deleted by run-hosted.sh; no receipt carries a value.
// Usage: hosted-env.mjs <hosted compose file> <out env file> <images json>
//   images json: {"BACKEND_IMAGE","BACKEND_DIGEST","GATEWAY_IMAGE","GATEWAY_DIGEST","FRONTEND_IMAGE"}
import { randomBytes } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** Fernet's key: url-safe base64 of 32 bytes, with its padding. */
export const fernetKey = () =>
  randomBytes(32).toString("base64").replace(/\+/g, "-").replace(/\//g, "_");

/** The fixed parts of the walk's hosted shape (stand-ins on the project network). */
export const HOSTED_WALK_FIXED = {
  BASE_URL: "http://localhost:8100",
  FRONTEND_URL: "http://localhost:3100",
  PG_HOST: "postgres",
  PG_PORT: "5432",
  PG_DB: "futureagi_hosted_walk",
  PG_USER: "hosted_walk",
  CH_HOST: "clickhouse",
  CH_PORT: "9000",
  CH_USER: "hosted_walk",
  TEMPORAL_HOST: "temporal:7233",
  AGENTCC_REDIS_ADDRESS: "redis:6379",
  AGENTCC_TRUSTED_PROXY_CIDR: "127.0.0.1/32",
};
const IMAGE_NAMES = [
  "BACKEND_IMAGE",
  "BACKEND_DIGEST",
  "GATEWAY_IMAGE",
  "GATEWAY_DIGEST",
  "FRONTEND_IMAGE",
];

/** Every variable the hosted file requires (`${NAME:?`). */
export const requiredNames = (compose) =>
  [
    ...new Set(
      [...compose.matchAll(/\$\{([A-Z_][A-Z0-9_]*):\?/g)].map((m) => m[1]),
    ),
  ].sort();

/** Pure but for randomness: the env for `compose`, given the local images. */
export function renderHostedEnv(compose, images) {
  for (const name of IMAGE_NAMES) {
    if (typeof images[name] !== "string" || !images[name])
      throw new Error(`hosted env: ${name} is required`);
  }
  if (
    !/^127\.0\.0\.1:\d+\//.test(images.BACKEND_IMAGE) ||
    !/^127\.0\.0\.1:\d+\//.test(images.GATEWAY_IMAGE)
  ) {
    throw new Error(
      "hosted env: images must come from the VM's loopback registry",
    );
  }
  for (const name of ["BACKEND_DIGEST", "GATEWAY_DIGEST"]) {
    if (!/^sha256:[0-9a-f]{64}$/.test(images[name]))
      throw new Error(`hosted env: ${name} is not a sha256 digest`);
  }
  const env = {};
  for (const name of requiredNames(compose))
    env[name] = randomBytes(32).toString("hex");
  Object.assign(env, HOSTED_WALK_FIXED, images, {
    INTEGRATION_ENCRYPTION_KEY: fernetKey(),
  });
  env.REDIS_URL = `redis://:${env.AGENTCC_REDIS_PASSWORD}@redis:6379/0`;
  return env;
}

export const toEnvFile = (env) =>
  `${Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n")}\n`;

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const [composeFile, out, imagesJson] = process.argv.slice(2);
  if (!composeFile || !out || !imagesJson) {
    console.error(
      "usage: hosted-env.mjs <hosted compose file> <out env file> <images json>",
    );
    process.exit(2);
  }
  const env = renderHostedEnv(
    readFileSync(composeFile, "utf8"),
    JSON.parse(imagesJson),
  );
  writeFileSync(out, toEnvFile(env), { mode: 0o600 });
  console.log(
    `hosted env: ${Object.keys(env).length} names written (values not printed)`,
  );
}

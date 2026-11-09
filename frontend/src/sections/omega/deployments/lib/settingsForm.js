// The Settings step of the wizard: deployment name, plain environment
// variables and secret references, validated with the rules EE applies
// (packages/server/src/workspace/project-deployments/{types,request}.ts).

import { injectionNameProblem, isExpired } from "./vaultNames";

// EE's vault resolves only upper-case injection names (rules in vaultNames.js).
export { SECRET_NAME_PATTERN } from "./vaultNames";
export const NAME_PATTERN = /^[a-z][a-z0-9-]{0,39}$/;
export const ENV_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
export const RESERVED_ENV_NAMES = [
  "OMEGA_SERVE_TOKEN",
  "OMEGA_DEPLOY_NAME",
  "OMEGA_DEPLOY_REVISION",
];
const MAX_ENV = 128;
const MAX_SECRETS = 64;
const MAX_VALUE_BYTES = 32 * 1024;

export function nameProblem(name) {
  if (!name) return "Name the deployment.";
  if (!NAME_PATTERN.test(name))
    return "Use lowercase letters, digits and dashes, starting with a letter (at most 40).";
  return null;
}

export function envNameProblem(name) {
  if (!ENV_NAME_PATTERN.test(name))
    return "Letters, digits and _ only, not starting with a digit.";
  if (RESERVED_ENV_NAMES.includes(name))
    return `${name} is set by the deployment itself.`;
  return null;
}

/** Rows [{ key, value }] -> { env, problems: { [rowIndex]: message } } */
export function envFromRows(rows, secretRefs = []) {
  const env = {};
  const problems = {};
  rows.forEach((row, index) => {
    const key = row.key.trim();
    if (!key && !row.value) return;
    const problem =
      (!key && "Add a name for this value.") ||
      envNameProblem(key) ||
      (Object.hasOwn(env, key) && `${key} is listed twice.`) ||
      (secretRefs.includes(key) && `${key} is also picked as a secret.`) ||
      (new TextEncoder().encode(row.value).length > MAX_VALUE_BYTES &&
        "Values are at most 32 KB.") ||
      (row.value.includes("\0") && "Values cannot hold a NUL character.");
    if (problem) problems[index] = problem;
    else env[key] = row.value;
  });
  if (Object.keys(env).length > MAX_ENV)
    problems.form = `At most ${MAX_ENV} variables.`;
  return { env, problems };
}

export const rowsFromEnv = (env = {}) =>
  Object.entries(env).map(([key, value]) => ({ key, value }));

/** Why EE would refuse to inject this name, or null (see vaultNames.js). */
function unusableReason(name, matches, now) {
  const fromName = injectionNameProblem(name);
  if (fromName) return fromName;
  if (matches.length > 1)
    return "More than one active credential has this name.";
  if (isExpired(matches[0], now))
    return "Expired: a deployment cannot receive it.";
  return null;
}

/**
 * Credentials a deployment can reference: active, one per name, with a name
 * and an expiry the vault will inject. The rest are listed with the reason.
 */
export function referenceableSecrets(credentials = [], now = Date.now()) {
  const byName = new Map();
  for (const credential of credentials) {
    if (credential.status !== "active") continue;
    byName.set(credential.name, [
      ...(byName.get(credential.name) || []),
      credential,
    ]);
  }
  return [...byName.entries()]
    .map(([name, matches]) => {
      const reason = unusableReason(name, matches, now);
      return { name, usable: reason === null, reason };
    })
    .sort((a, b) => (a.name < b.name ? -1 : 1));
}

/**
 * What stops the picked secrets from being sent, or null. A redeploy starts
 * from an earlier revision's names, which may predate the injection rules
 * (vaultNames.js). The vault would refuse such a name (an older EE only when
 * the container starts, after a full build), so the wizard holds the deploy
 * until it is unticked.
 */
export function secretRefsProblem(secretRefs = []) {
  if (secretRefs.length > MAX_SECRETS) return `At most ${MAX_SECRETS} secrets.`;
  const blocked = secretRefs.filter((ref) => injectionNameProblem(ref));
  if (blocked.length === 0) return null;
  return `Untick ${blocked.join(", ")} to continue: a deployment cannot receive ${blocked.length === 1 ? "it" : "them"}.`;
}

export function settingsProblems({ name, rows, secretRefs }) {
  const { env, problems } = envFromRows(rows, secretRefs);
  const nameError = nameProblem(name);
  const secretError = secretRefsProblem(secretRefs);
  return {
    env,
    nameError,
    rowProblems: problems,
    secretError,
    ok: !nameError && Object.keys(problems).length === 0 && !secretError,
  };
}

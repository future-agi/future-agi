// The secret forms (add, rotate). Values follow EE's write check
// (credential-vault.ts assertSecret: not empty, at most 16 KB) and hold no
// NUL, which no environment variable can carry. Names are stricter than EE's
// create, which takes any text: a deployment receives a secret under its
// name, so a name must pass the gate EE applies when the container starts
// (vaultNames.js), must not be one the deployment sets itself, and must be
// unique (project-deployments/secrets.ts refuses an ambiguous reference).

import { OMEGA_STATE } from "src/sections/omega/shared/omegaStates";

import { RESERVED_ENV_NAMES } from "./settingsForm";
import { SECRET_NAME_PATTERN, reservedNameReason } from "./vaultNames";

export const SECRET_KINDS = [
  { value: "api_key", label: "API key" },
  { value: "generic", label: "Other secret" },
];
const KIND_VALUES = SECRET_KINDS.map((kind) => kind.value);
const MAX_SECRET_BYTES = 16 * 1024;

const bytes = (text) => new TextEncoder().encode(text).length;

/** The vault's active names, for the uniqueness check. */
export const activeNames = (credentials = []) =>
  new Set(
    credentials
      .filter((credential) => credential.status === "active")
      .map((credential) => credential.name),
  );

export function secretNameProblem(name, taken = new Set()) {
  if (!name) return "Name the secret.";
  if (!SECRET_NAME_PATTERN.test(name))
    return "Use A-Z, 0-9 and _, starting with a letter or _ (the name the project reads).";
  if (RESERVED_ENV_NAMES.includes(name))
    return `${name} is set by the deployment itself.`;
  const reserved = reservedNameReason(name);
  if (reserved) return `${reserved} Pick another name.`;
  if (taken.has(name))
    return `${name} is already in the vault. Replace its value instead.`;
  return null;
}

/** A value is taken exactly as typed: never trimmed, never shown again. */
export function secretValueProblem(value) {
  if (!value) return "Enter the value.";
  if (bytes(value) > MAX_SECRET_BYTES) return "Values are at most 16 KB.";
  if (value.includes("\0")) return "Values cannot hold a NUL character.";
  return null;
}

export function addSecretProblems({ name, kind, value }, taken) {
  const nameError = secretNameProblem(name, taken);
  const valueError = secretValueProblem(value);
  const kindError = KIND_VALUES.includes(kind) ? null : "Pick a kind.";
  return {
    nameError,
    valueError,
    kindError,
    ok: !nameError && !valueError && !kindError,
  };
}

/**
 * Who can change the vault. EE lets workspace owners and admins write
 * (credential-vault-routes.ts canMutate), and Future AGI's session exchange
 * makes an organization Owner an Omega owner and an Admin an admin
 * (accounts/omega_session.py delegation_role); every other role is a member.
 */
export const canManageSecretsFor = (role) =>
  role === "Owner" || role === "Admin";

/**
 * "Used in 2 places" from a public record's bindingCount. A binding is made
 * when something that picks the secret first starts: a deployment, or an
 * agent in the Omega Console, so the count is not deployments alone.
 */
export function usageLabel(bindingCount) {
  if (!bindingCount) return "Not used yet";
  return bindingCount === 1
    ? "Used in 1 place"
    : `Used in ${bindingCount} places`;
}

export const kindLabel = (kind) =>
  SECRET_KINDS.find((entry) => entry.value === kind)?.label ??
  (kind === "oauth_token" ? "OAuth token" : "Secret");

/**
 * What a failed write left behind, said plainly. A refusal (4xx) changed
 * nothing. When Omega did not answer, the write may or may not have landed;
 * saving the same entries again is safe because they carry the same
 * Idempotency-Key (secrets/useSecrets.js).
 */
export function afterFailure(error, { refused, unknown }) {
  return error?.state === OMEGA_STATE.UNAVAILABLE ? unknown : refused;
}

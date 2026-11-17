// The one place Future AGI states which vault secrets a deployment can
// receive. It mirrors the gate EE applies when it materializes a deployment's
// secrets (packages/server/src/workspace/credential-vault.ts at 8744f580:
// resolveEnvironmentCredentials -> assertSafeEnvironmentInjectionName,
// isReservedEnvironmentInjectionName, expired). EE does not apply this gate
// when a secret is created or a deploy is accepted, only when the container
// starts, so a name or secret it refuses must be stopped here, before a build.

/** Upper-case environment names: the only names the vault injects. */
export const SECRET_NAME_PATTERN = /^[A-Z_][A-Z0-9_]{0,127}$/;

/** System names the vault never overrides in a deployment's environment. */
export const VAULT_RESERVED_NAMES = Object.freeze([
  "PATH",
  "HOME",
  "SHELL",
  "PWD",
  "OLDPWD",
  "SHLVL",
  "LANG",
  "TERM",
  "TMP",
  "TEMP",
  "TMPDIR",
  "SYSTEMROOT",
  "WINDIR",
  "NODE_OPTIONS",
  "NODE_PATH",
  "BASH_ENV",
  "ENV",
  "CDPATH",
  "IFS",
  "PYTHONPATH",
  "PYTHONHOME",
  "RUBYOPT",
  "PERL5OPT",
]);

/** Prefixes kept for Omega itself and for the system loader. */
export const VAULT_RESERVED_PREFIXES = Object.freeze([
  { prefix: "OMEGA_", owner: "Omega" },
  { prefix: "LD_", owner: "the system loader" },
  { prefix: "DYLD_", owner: "the system loader" },
]);

/**
 * Why a deployment cannot receive a secret under this (well-formed) name, or
 * null when it can. The sentence suits both the add form and the picker.
 */
export function reservedNameReason(name) {
  if (VAULT_RESERVED_NAMES.includes(name))
    return `${name} is a system variable a deployment cannot receive.`;
  const kept = VAULT_RESERVED_PREFIXES.find(({ prefix }) =>
    name.startsWith(prefix),
  );
  return kept
    ? `Names starting ${kept.prefix} are kept for ${kept.owner}; a deployment cannot receive them.`
    : null;
}

/**
 * Why a deployment cannot receive a secret under this name, judged from the
 * name alone (the add form, the picker, and the names a redeploy carries
 * over from an earlier revision), or null when it can.
 */
export function injectionNameProblem(name) {
  if (!SECRET_NAME_PATTERN.test(name))
    return "Only upper-case names (A-Z, 0-9, _) can be injected.";
  return reservedNameReason(name);
}

/** EE refuses an expired secret at start; a missing or bad date never expires. */
export function isExpired(credential, now = Date.now()) {
  const at = Date.parse(credential?.expiresAt ?? "");
  return Number.isFinite(at) && at <= now;
}

/**
 * Environment-variables screen (§11): the three groups the §6 detail exposes,
 * each read from its own split field on `settings.agent`.
 *
 * Never read `secret_refs` here — it is the union of `secrets` and
 * `credential_files` kept for older callers, so reading it would list every
 * credential file twice. New code reads the split fields.
 *
 * The contract returns no value for a secret or a credential file (they are
 * encrypted at rest and only ever addressed by name), so those two groups carry
 * names only; `config` is the one group that carries values. Names in `secrets`
 * that are not truly secret (LIVEKIT_URL, GOOGLE_CLOUD_PROJECT) are left where
 * the submitter put them — the contract says to render them as classified, not
 * to second-guess.
 */
export function envVarGroups(agent) {
  const a = agent || {};
  return {
    secrets: Array.isArray(a.secrets) ? a.secrets.filter(Boolean) : [],
    config: a.config && typeof a.config === "object" ? a.config : {},
    credentialFiles: Array.isArray(a.credential_files)
      ? a.credential_files.filter((f) => f && f.environment_name)
      : [],
  };
}

/** True when no group holds anything to show. */
export function envVarsEmpty(groups) {
  return (
    !groups.secrets.length &&
    !Object.keys(groups.config).length &&
    !groups.credentialFiles.length
  );
}

// The raw `{ ALIAS: value }` credential map for the ONE preflight that follows a
// build handoff. The backend's live `credentials_valid` / `provider_target`
// probes read the write-only `credential_values` field, so the plaintext has to
// reach that request — but it must not be persisted anywhere on the way:
//
//   - not the zustand draft (sessionStorage-persisted, and mirrored into redux
//     devtools outside production),
//   - not the react-query key (which is serialised into the cache),
//   - not the mutation cache.
//
// So it lives here, in module memory, for the lifetime of the tab. A refresh
// drops it: the draft survives, the probe then reports the credential missing,
// which is the honest answer — the value is gone.
let pending = {};

export function setPendingCredentialValues(values) {
  pending = values && Object.keys(values).length ? { ...values } : {};
}

// Read, never consumed: "Retry read" re-runs the same preflight and must probe
// with the same values.
export function getPendingCredentialValues() {
  return pending;
}

export function clearPendingCredentialValues() {
  pending = {};
}

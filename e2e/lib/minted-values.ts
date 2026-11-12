// The values this worker MINTED for its synthetic actors (passwords,
// access/refresh tokens, org API/secret keys, invitation tokens). Kept in
// memory only, in the worker that minted them, and handed to the evidence
// auditor's exact-match detector (lib/trace-hygiene/hook.ts). Never written to
// disk, attached or logged. The trace scrubber does not read it: the auditor's
// registry check stays independent of the scrubber's rules.
const minted = new Set<string>();

export function mint(...values: unknown[]): void {
  for (const v of values) if (typeof v === 'string' && v.length >= 6) minted.add(v);
}

export function mintedValues(): string[] {
  return [...minted];
}

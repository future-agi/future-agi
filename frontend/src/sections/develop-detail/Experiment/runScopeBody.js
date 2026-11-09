import { isSurfaceShown } from "src/launch/launchSwitch";

// F50's split_ids is deliberately independent of the F05 scope boundary.
export const SCOPE_FIELDS = [
  "row_ids",
  "sample",
  "metadata",
  "error_policy",
  "limits",
];

const policyKey = (value) =>
  JSON.stringify(
    Object.entries(value ?? {}).sort(([a], [b]) => a.localeCompare(b)),
  );

/** Translate wizard settings without changing the meaning of any run's limits. */
export function runScopeBody(body) {
  const out = { ...body };
  if (isSurfaceShown("exp.new.scope")) {
    out.error_policy = body?.error_policy ?? "zero";
  } else if (
    out.metadata &&
    !Array.isArray(out.metadata) &&
    Object.keys(out.metadata).length === 0
  ) {
    // The wizard always builds metadata={}. It means legacy behavior, not F05.
    // Nonempty values remain for serverBody to refuse, never silently discard.
    delete out.metadata;
  }
  if (Array.isArray(out.split_ids) && out.split_ids.length === 0)
    delete out.split_ids;
  if (body?.sample?.n != null && body.sample.size == null) {
    const { n, ...sample } = body.sample;
    out.sample = { ...sample, size: n };
  }
  const entries = body?.prompt_config ?? [];
  const limits = entries[0]?.row_limits;
  // The server currently has one experiment-wide policy. Only identical
  // limits on EVERY run can be lifted safely. Leave mixed limits nested so
  // serverBody rejects them rather than silently limiting an unlimited run.
  if (
    limits &&
    Object.keys(limits).length &&
    entries.every(
      (entry) => policyKey(entry.row_limits) === policyKey(limits),
    ) &&
    (body?.limits == null || policyKey(body.limits) === policyKey(limits))
  ) {
    out.limits = { ...limits };
    out.prompt_config = entries.map(
      ({ row_limits: _limits, ...entry }) => entry,
    );
  }
  return out;
}

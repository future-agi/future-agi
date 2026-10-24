import { SAMPLING_QUOTA_LIMIT } from "./samplingPolicyForm";

// Cell identities are opaque preview values. Do not turn them into extra slice
// predicates: that would change the first-match classification of a contact
// whose rows have different group-by values.
export function applyCellQuotas(policy, cells, quotas) {
  if (policy.mode !== "closed")
    throw new Error("Cell quotas require a closed window.");
  if (!cells.length || cells.length !== quotas.length || cells.length > 1000)
    throw new Error(
      "Preview between 1 and 1,000 cells before applying quotas.",
    );
  const overrides = { ...(policy.cell_quotas || {}) };
  const seen = new Set();
  cells.forEach((cell, i) => {
    const quota = quotas[i];
    if (
      !Number.isSafeInteger(quota) ||
      quota < 0 ||
      quota > SAMPLING_QUOTA_LIMIT
    )
      throw new Error("Each cell quota must be from 0 to 1,000,000.");
    if (typeof cell.stratum !== "string" || seen.has(cell.stratum))
      throw new Error("Preview cell identities must be unique.");
    seen.add(cell.stratum);
    overrides[cell.stratum] = quota;
  });
  const next = { ...policy, cell_quotas: overrides };
  if (
    Object.keys(overrides).length > 1000 ||
    new TextEncoder().encode(JSON.stringify(next)).length > 65536
  )
    throw new Error(
      "These quotas exceed the policy size limit. Narrow the group-by attributes.",
    );
  return next;
}

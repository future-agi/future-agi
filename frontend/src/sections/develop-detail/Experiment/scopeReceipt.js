import { rowLimitsLine } from "src/sections/common/RunSettings/rowLimitsModel";

/**
 * How errored tries count, in the Review step's words. No policy is the
 * default: the server gives an errored try no score and leaves it out of
 * every average. "zero" and "exclude" are what a person can choose.
 */
export function errorPolicyLine(policy) {
  if (policy === "exclude") return "Errors excluded from scores";
  if (policy === "zero") return "Errors count as 0 in scores";
  return "Errors get no score and stay out of averages (default)";
}

/** A receipt is derived from persisted experiment detail, never from draft counts. */
export function scopeReceipt(
  detail = {},
  { includeScopeSettings = true } = {},
) {
  const scope = detail.run_scope ?? {};
  const examples =
    Number.isInteger(scope.selected_count) && scope.selected_count >= 0
      ? scope.selected_count
      : null;
  const lines = [];
  if (examples != null) {
    lines.push(
      `Server selected ${examples} of ${scope.eligible_count} eligible rows (${scope.source_count} source rows)`,
    );
  }
  // F50 may show selection counts with F05 held. Do not surface F05 settings
  // even if a stale/cached server response still contains them.
  if (includeScopeSettings) {
    if (scope.sample)
      lines.push(
        `Sample size ${scope.sample.size} · seed ${scope.sample.seed}`,
      );
    const limits = rowLimitsLine(detail.limits);
    if (limits) lines.push(limits);
    if (detail.error_policy === "exclude" || detail.error_policy === "zero")
      lines.push(errorPolicyLine(detail.error_policy));
  }
  return { examples, lines };
}

import { rowLimitsLine } from "src/sections/common/RunSettings/rowLimitsModel";

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
    if (detail.error_policy === "exclude")
      lines.push("Errors excluded from scores");
    if (detail.error_policy === "zero")
      lines.push("Errors count as 0 in scores");
  }
  return { examples, lines };
}

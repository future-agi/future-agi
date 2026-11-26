// What a severity means for this issue: its reason, or that it isn't
// established yet. Shared by the severity badge and the Triage dropdown.
export function severityExplanation({
  severity,
  assessmentStatus,
  source,
  reason,
  label,
}) {
  const fallback = source === "default" && assessmentStatus !== "completed";
  if (fallback) {
    return `Severity has not been established (${assessmentStatus || "unassessed"}). Medium is only a compatibility default. ${reason || ""}`.trim();
  }
  return `${reason || label || severity || ""}${assessmentStatus === "pending" ? " — reassessment pending" : ""}`;
}

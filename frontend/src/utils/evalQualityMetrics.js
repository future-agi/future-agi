// Quality scores and execution outcomes are different populations. In
// particular, an all-error bucket is unscored, not a quality score of zero.

const qualityCountKeys = [
  "count",
  "scored_count",
  "unscored_count",
  "completed_count",
  "error_count",
  "pending_count",
  "running_count",
  "skipped_count",
  "failed_count",
];

export const isValidEvalQualityPoint = (point) =>
  qualityCountKeys.every(
    (key) => Number.isSafeInteger(point?.[key]) && point[key] >= 0,
  ) &&
  point.scored_count + point.unscored_count === point.count &&
  point.scored_count <= point.completed_count &&
  point.failed_count <= point.scored_count &&
  point.completed_count +
    point.error_count +
    point.pending_count +
    point.running_count +
    point.skipped_count <=
    point.count;

// Preserve the reviewed NK20 exact-source copy contract.
// prettier-ignore
export function evalQualitySummary(points = [], isEvaluation) {
  if (isEvaluation === false) return "";
  if (!Array.isArray(points)) return "Unavailable";
  if (
    isEvaluation !== true &&
    !points.some((point) =>
      qualityCountKeys.some((key) => key !== "count" && point?.[key] != null),
    )
  ) return "";
  if (points.some((point) => !isValidEvalQualityPoint(point)))
    return "Unavailable";
  const measured = points;
  if (measured.every((point) => point.count === 0)) return "No data";
  const count = (key) =>
    measured.reduce((sum, point) => sum + point[key], 0);
  if (!qualityCountKeys.every((key) => Number.isSafeInteger(count(key))))
    return "Unavailable";
  const scored = count("scored_count");
  const errors = count("error_count");
  const terminal = errors + count("completed_count");
  return [
    scored ? `${scored} scored` : "Unscored",
    `${errors} execution ${errors === 1 ? "error" : "errors"}${terminal ? ` (${((100 * errors) / terminal).toFixed(1)}%)` : ""}`,
    `${count("failed_count")} quality ${count("failed_count") === 1 ? "failure" : "failures"}`,
    `${count("pending_count")} pending`,
    `${count("running_count")} running`,
    `${count("skipped_count")} skipped`,
    `${count("unscored_count")} without a quality score`,
  ].join(" · ");
}

/**
 * Extract the raw value from an analytics field.
 *
 * The analytics overview API returns every metric as `{ value, trend }`.
 * Components that display these values must unwrap them first.
 *
 *   val({ value: 42, trend: 5 })  => 42
 *   val(42)                       => 42
 *   val(null)                     => null
 */
export function val(obj) {
  if (obj != null && typeof obj === "object" && "value" in obj)
    return obj.value;
  return obj;
}

/**
 * Extract the trend percentage from an analytics field.
 *
 *   trend({ value: 42, trend: 5.2 })  => 5.2
 *   trend(42)                          => null
 */
export function trend(obj) {
  if (obj != null && typeof obj === "object" && "trend" in obj)
    return obj.trend;
  return null;
}

/**
 * Bucket size to ask the analytics API for over a time range.
 *
 * Returns a value the API accepts ("minute", "hour" or "day"); it serves
 * anything else as "hour", whatever the range.
 *
 *   up to 6 hours  => "minute"
 *   up to 7 days   => "hour"
 *   longer         => "day"
 */
export function computeGranularity(start, end) {
  if (!start || !end) return "hour";
  const diffMs = new Date(end).getTime() - new Date(start).getTime();
  const diffHours = diffMs / (1000 * 60 * 60);
  if (diffHours <= 6) return "minute";
  if (diffHours <= 168) return "hour"; // 7 days
  return "day";
}

export function isCallDetailAccessError(error) {
  return [401, 403, 404].includes(error?.statusCode ?? error?.response?.status);
}

const ANALYSIS_SCHEDULING_WINDOW_MS = 2 * 60 * 1000;

function isAwaitingAudioScheduling(data) {
  if (data?.status !== "completed" || data.is_snapshot) return false;
  const audio = data.audio_metrics;
  const metrics = Object.values(audio?.metrics ?? {});
  if (
    audio?.state !== "not_requested" ||
    !metrics.length ||
    !metrics.every((metric) => metric?.reason === "not_analyzed")
  ) {
    return false;
  }

  // Both detail serializers expose start_time + duration_seconds, not an end
  // timestamp. Never use their mutable `timestamp` (updated_at) for recency.
  const duration = data.duration_seconds ?? data.duration;
  const endedAt = data.ended_at ?? data.completed_at;
  const endedMs = endedAt
    ? Date.parse(endedAt)
    : Number.isFinite(duration) && duration >= 0
      ? Date.parse(data.start_time) + duration * 1000
      : NaN;
  const age = Date.now() - endedMs;
  return age >= 0 && age < ANALYSIS_SCHEDULING_WINDOW_MS;
}

// Both detail APIs use this policy; the legacy hook unwraps its Axios cache.
export function callDetailRefetchInterval(data, error) {
  if (isCallDetailAccessError(error)) return false;
  const evalMetrics = data?.eval_metrics;
  const isLocalizing =
    evalMetrics &&
    typeof evalMetrics === "object" &&
    Object.values(evalMetrics).some((metric) =>
      ["pending", "running"].includes(metric?.error_localizer_status),
    );
  if (isLocalizing) return 3000;
  const audio = data?.audio_metrics;
  if (
    audio?.state === "pending" &&
    (!audio.deadline_at || Date.parse(audio.deadline_at) > Date.now())
  ) {
    return 5000;
  }
  return isAwaitingAudioScheduling(data) ? 5000 : false;
}

export function callDetailRetry(retry = 3) {
  return (failureCount, error) => {
    if (isCallDetailAccessError(error)) return false;
    if (typeof retry === "function") return retry(failureCount, error);
    return (
      retry === true || (typeof retry === "number" && failureCount < retry)
    );
  };
}

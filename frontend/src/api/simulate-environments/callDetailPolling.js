export function isCallDetailAccessError(error) {
  return [401, 403, 404].includes(error?.statusCode ?? error?.response?.status);
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
  return audio?.state === "pending" &&
    (!audio.deadline_at || Date.parse(audio.deadline_at) > Date.now())
    ? 5000
    : false;
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

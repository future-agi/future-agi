import { useEffect, useRef, useState } from "react";
import axios, { endpoints } from "src/utils/axios";

/**
 * Polls `/get-eval-logs?log_id=<id>` for async error-localization results
 * created by the eval playground when `error_localizer: true` is sent.
 *
 * Returns a state object `{ status, details, message }` that the caller can
 * merge into the eval result shape read by `EvalResultDisplay`:
 *   - status: "pending" | "running" | "completed" | "failed" | null
 *   - details: the `error_details` block from the backend
 *     (`{ error_analysis, selected_input_key, input_types, input_data }`)
 *   - message: non-null when the backend reports a failure
 *
 * Usage:
 *
 *   const { state, start, reset } = useErrorLocalizerPoll();
 *   ...
 *   const { data } = await axios.post(evalPlayground, payload);
 *   if (errorLocalizerEnabled && data?.result?.log_id) {
 *     start(data.result.log_id);
 *   }
 *   setResult({ ...data.result, ...state });
 */
export function useErrorLocalizerPoll({
  intervalMs = 2000,
  timeoutMs = 120000,
} = {}) {
  const [state, setState] = useState({
    status: null,
    details: null,
    message: null,
  });
  const timerRef = useRef(null);
  const abortedRef = useRef(false);
  const startedAtRef = useRef(0);

  const stop = () => {
    abortedRef.current = true;
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const reset = () => {
    stop();
    abortedRef.current = false;
    setState({ status: null, details: null, message: null });
  };

  const poll = async (logId) => {
    if (abortedRef.current || !logId) return;
    if (Date.now() - startedAtRef.current > timeoutMs) {
      // Give up gracefully — keep whatever status we last saw
      return;
    }
    try {
      const { data } = await axios.get(endpoints.develop.eval.getEvalLogs, {
        params: { log_id: logId, source: "logs" },
      });
      if (abortedRef.current) return;
      const row = data?.result || {};
      const status = row.error_localizer_status || null;
      const details = row.error_details || null;
      const message = row.error_localizer_message || null;
      setState({ status, details, message });

      // Keep polling until the task reaches a terminal state.
      const terminal =
        status === "completed" || status === "failed" || status === "skipped";
      if (details && (details.error_analysis || Array.isArray(details))) {
        return;
      }
      if (!terminal) {
        timerRef.current = setTimeout(() => poll(logId), intervalMs);
      }
    } catch {
      // Network hiccup — back off once then retry until timeout.
      if (!abortedRef.current) {
        timerRef.current = setTimeout(() => poll(logId), intervalMs * 2);
      }
    }
  };

  const start = (logId) => {
    reset();
    if (!logId) return;
    abortedRef.current = false;
    startedAtRef.current = Date.now();
    // Seed an immediate "running" state so the UI shows the spinner
    // straight away while the first poll is in flight.
    setState({ status: "running", details: null, message: null });
    poll(logId);
  };

  // Clean up on unmount
  useEffect(() => () => stop(), []);

  return { state, start, reset };
}

export default useErrorLocalizerPoll;

// The CallValue of each lens lane: parsed from the lane value
// when it holds the whole call (turns included), else read through F's
// `useCall` hook by its call id (a grid output may be a cut JSON string).
// Up to 4 lanes (the lens maximum), so the hook count stays fixed.
import { useMemo } from "react";
import { useCall } from "src/api/develop/r4";
import { callOf } from "src/sections/common/Media/mediaRef";

export const MAX_LANES = 4;

/** The call id in a value, even a cut JSON string. */
export function callIdOf(value) {
  const call = callOf(value);
  if (call) return call.call_id;
  if (typeof value === "string") {
    const m = /"call_id"\s*:\s*"([^"]+)"/.exec(value);
    if (m) return m[1];
  }
  return null;
}

const complete = (call) => Boolean(call && Array.isArray(call.turns));

/**
 * @param {{value: *}[]} lanes
 * @returns {{calls: (object|null)[], loading: boolean, error: boolean,
 *   refetch: () => void}}
 */
export default function useLaneCalls(lanes) {
  const values = (lanes ?? []).slice(0, MAX_LANES).map((l) => l?.value);
  const parsed = values.map((v) => callOf(v));
  const ids = values.map((v, i) => (complete(parsed[i]) ? null : callIdOf(v)));
  const q0 = useCall(ids[0], { enabled: Boolean(ids[0]) });
  const q1 = useCall(ids[1], { enabled: Boolean(ids[1]) });
  const q2 = useCall(ids[2], { enabled: Boolean(ids[2]) });
  const q3 = useCall(ids[3], { enabled: Boolean(ids[3]) });
  const qs = [q0, q1, q2, q3];
  const key = values
    .map((v, i) => `${ids[i]}:${typeof v === "string" ? v.length : v ? 1 : 0}`)
    .join("|");
  const dataKey = qs.map((q) => (q.data ? q.data.call_id : "")).join("|");
  const calls = useMemo(
    () =>
      values.map((_, i) => {
        if (complete(parsed[i])) return parsed[i];
        return qs[i].data ?? null;
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, dataKey],
  );
  const loading = ids.some((id, i) => id && qs[i].isLoading);
  const error = ids.some((id, i) => id && qs[i].isError);
  return {
    calls,
    loading,
    error,
    refetch: () => qs.forEach((q, i) => ids[i] && q.refetch()),
  };
}

// A Usage run's source trace and evaluated span. Session-level runs have
// neither; trace-level runs carry the trace's root span.
export function getRunTraceId(run) {
  return run?.trace_id || run?.detail?.trace_id || null;
}

export function getRunSpanId(run) {
  return run?.span_id || run?.detail?.span_id || null;
}

// Index of the nearest run in `step` direction (+1 / -1) whose trace differs
// from the run at `fromIndex`, or -1. Several evals usually score the same
// trace, so stepping run by run would leave the trace drawer on one trace.
export function findAdjacentTraceRunIndex(runs, fromIndex, step) {
  const currentTraceId = getRunTraceId(runs[fromIndex]);
  for (let i = fromIndex + step; i >= 0 && i < runs.length; i += step) {
    const traceId = getRunTraceId(runs[i]);
    if (traceId && traceId !== currentTraceId) return i;
  }
  return -1;
}

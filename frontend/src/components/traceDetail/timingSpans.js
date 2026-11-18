/** Only producer-qualified, tokenless timing activity is hidden by default. */
export function isTimingSpan(span) {
  return (
    span?.observation_type === "internal" &&
    span?.span_attributes?.["fi.observation.category"] === "timing" &&
    span?.status !== "ERROR" &&
    !span?.total_tokens &&
    !span?.prompt_tokens &&
    !span?.completion_tokens &&
    !span?.cost
  );
}

export function timingSpanIds(entries = []) {
  const ids = new Set();
  const pending = [...entries];
  while (pending.length) {
    const entry = pending.pop();
    const span = entry.observation_span || {};
    if (isTimingSpan(span)) ids.add(span.id);
    pending.push(...(entry.children || []));
  }
  return ids;
}

/** Promote real work beneath hidden timing wrappers; never lose a subtree. */
export function withoutTimingSpans(entries = []) {
  return entries.flatMap((entry) => {
    const children = withoutTimingSpans(entry.children || []);
    return isTimingSpan(entry.observation_span)
      ? children
      : [{ ...entry, children }];
  });
}

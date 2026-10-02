// Add-result toast helpers — report what the BACKEND actually did, not what we
// asked for. The `add-items` endpoint returns 200 with `{added, duplicates,
// errors}` even when it adds nothing (a source that fails to resolve, is
// unavailable, or is already queued), so echoing the requested count reported
// phantom successes. Read the real counts, accumulate across batched requests,
// and on `added === 0` surface the reason instead of a false "N added".

export function summarizeAddResults(responses) {
  return responses.reduce(
    (acc, resp) => {
      const r = resp?.data?.result || resp?.data || {};
      const pageErrors = Array.isArray(r.errors) ? r.errors : [];
      acc.added += r.added || 0;
      acc.duplicates += r.duplicates || 0;
      acc.errorCount +=
        Number.isSafeInteger(r.error_count) && r.error_count >= 0
          ? r.error_count
          : pageErrors.length;
      acc.errors.push(...pageErrors);
      return acc;
    },
    { added: 0, duplicates: 0, errors: [], errorCount: 0 },
  );
}

export function addResultToast({
  added,
  duplicates,
  errors,
  errorCount = errors.length,
}) {
  const n = (c) => `${c} item${c !== 1 ? "s" : ""}`;
  if (added > 0) {
    const extra = [];
    if (duplicates) extra.push(`${duplicates} already in queue`);
    if (errorCount) extra.push(`${n(errorCount)} skipped`);
    return {
      message: extra.length
        ? `${n(added)} added · ${extra.join(" · ")}`
        : `${n(added)} added to queue`,
      variant: errorCount ? "warning" : "success",
    };
  }
  if (errorCount) {
    return {
      message: `Couldn't add ${n(errorCount)}: ${errors[0] || "Some items could not be added"}`,
      variant: "error",
    };
  }
  if (duplicates) {
    return {
      message: `${n(duplicates)} already in the queue — nothing to add`,
      variant: "info",
    };
  }
  return { message: "No items were added", variant: "warning" };
}

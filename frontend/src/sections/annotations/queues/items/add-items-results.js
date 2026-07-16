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
      acc.added += r.added || 0;
      acc.duplicates += r.duplicates || 0;
      if (Array.isArray(r.errors)) acc.errors.push(...r.errors);
      return acc;
    },
    { added: 0, duplicates: 0, errors: [] },
  );
}

export function addResultToast({ added, duplicates, errors }) {
  const n = (c) => `${c} item${c !== 1 ? "s" : ""}`;
  if (added > 0) {
    const extra = [];
    if (duplicates) extra.push(`${duplicates} already in queue`);
    if (errors.length) extra.push(`${n(errors.length)} skipped`);
    return {
      message: extra.length
        ? `${n(added)} added · ${extra.join(" · ")}`
        : `${n(added)} added to queue`,
      variant: errors.length ? "warning" : "success",
    };
  }
  if (errors.length) {
    return { message: `Couldn't add ${n(errors.length)}: ${errors[0]}`, variant: "error" };
  }
  if (duplicates) {
    return {
      message: `${n(duplicates)} already in the queue — nothing to add`,
      variant: "info",
    };
  }
  return { message: "No items were added", variant: "warning" };
}

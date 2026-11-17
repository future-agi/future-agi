// v2.1 g8 (door:run-on-rows:*): a New experiment opened on picked rows
// says so in the URL (`new_experiment=1&rows=<ids>`), so a reload reopens
// the drawer on the same rows (RunExperiment's deep link reads `rows=`).
// Pure; `replaceUrl` is the caller's history write.

/** The search string with the drawer open on these row ids. */
export function withExperimentRows(search, rowIds) {
  const next = new URLSearchParams(String(search ?? "").replace(/^\?/, ""));
  const ids = (rowIds ?? []).map(String).filter(Boolean);
  next.set("new_experiment", "1");
  if (ids.length) next.set("rows", ids.join(","));
  else next.delete("rows");
  return `?${next.toString()}`;
}

/** Write it into the current URL without a navigation (replace). */
export function writeExperimentRows(rowIds) {
  if (typeof window === "undefined") return;
  const search = withExperimentRows(window.location.search, rowIds);
  window.history.replaceState(
    window.history.state,
    "",
    `${window.location.pathname}${search}${window.location.hash}`,
  );
}

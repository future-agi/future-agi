// L7c g1 (url:dataset): the dataset page's tabs. An unknown `?tab=` falls
// back to Data with a one-line notice instead of a blank body. Pure.
export const DATASET_TABS = Object.freeze([
  "data",
  "experiments",
  "summary",
  "optimization",
]);

/**
 * null when `tab` is absent or known; else the search string with tab=data
 * and the notice to show.
 */
export function unknownTabFallback(search) {
  const q = new URLSearchParams(search ?? "");
  const tab = q.get("tab");
  if (tab == null || DATASET_TABS.includes(tab)) return null;
  q.set("tab", "data");
  return {
    search: `?${q.toString()}`,
    notice: `This dataset has no “${tab}” tab, so it opened on Data.`,
  };
}

/**
 * v2.1 g8 (header:more): the dataset page's Delete goes through the list's
 * one delete — the counted confirm, then the list hides the dataset with a
 * 10 s Undo before the delete is sent. The page hands the dataset over in
 * router state; the list takes it once. Pure.
 */
export const deleteHandoffState = (row = {}) => ({
  deleteDatasets: [
    {
      id: row.id,
      name: row.name ?? "",
      numberOfExperiments: Number(row.numberOfExperiments) || 0,
    },
  ],
});

/** The datasets a router state hands to the list's delete, or null. */
export function handedDeleteOf(state) {
  const list = state?.deleteDatasets;
  if (!Array.isArray(list)) return null;
  const rows = list.filter((d) => d && d.id);
  return rows.length ? rows : null;
}

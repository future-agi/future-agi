/** The page a list row opens: the experiment's Data view. */
export function classicExperimentPath(row, datasetId) {
  const encoded = encodeURIComponent(
    JSON.stringify({ id: row.id, name: row.name }),
  );
  return `/dashboard/develop/experiment/${row.id}/data?datasetId=${datasetId}&payload=${encoded}`;
}

export function getCreatedDatasetId(response) {
  const result = response?.data?.result || response?.result || {};
  return result.dataset_id || result.datasetId || null;
}

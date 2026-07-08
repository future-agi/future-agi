export function getCreatedDatasetName(response) {
  const result = response?.data?.result || response?.result || {};
  return result.dataset_name || result.datasetName || result.name || null;
}

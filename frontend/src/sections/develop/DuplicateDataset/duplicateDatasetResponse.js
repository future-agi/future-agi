export function getClonedDatasetInfo(response) {
  const result = response?.data?.result || response?.result || {};
  return {
    datasetId: result.dataset_id || result.datasetId || result.id || null,
    datasetName:
      result.dataset_name || result.datasetName || result.name || null,
  };
}

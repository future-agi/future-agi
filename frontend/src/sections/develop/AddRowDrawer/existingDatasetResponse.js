export function getCreatedDatasetCopyId(response) {
  const result = response?.data?.result || response?.result || {};
  return (
    result.dataset_id ||
    result.datasetId ||
    result.new_dataset_id ||
    result.newDatasetId ||
    null
  );
}

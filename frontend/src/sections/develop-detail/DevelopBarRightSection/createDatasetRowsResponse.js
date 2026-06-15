export function getCreatedRowsDatasetId(response) {
  const result = response?.data?.result || response?.result || {};
  return (
    result.new_dataset_id ||
    result.newDatasetId ||
    result.dataset_id ||
    result.datasetId ||
    null
  );
}

export function getAddRowsFromExistingPayload({
  sourceDatasetId,
  columnMapping,
  numRows,
}) {
  const payload = {
    source_dataset_id: sourceDatasetId,
    column_mapping: columnMapping,
  };
  // A blank row count imports every source row, so it is left out rather
  // than sent as null.
  if (numRows !== "" && numRows !== null && numRows !== undefined) {
    payload.num_rows = numRows;
  }
  return payload;
}

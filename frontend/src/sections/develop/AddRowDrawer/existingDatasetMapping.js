export function getExistingDatasetConfigValues(
  sourceColumns = [],
  { isAddingToExistingDataset = false, targetColumns = [] } = {},
) {
  const mapping = {};
  const targetColumnIdByName = new Map(
    targetColumns
      .filter((column) => column?.name && column?.id)
      .map((column) => [column.name, column.id]),
  );

  if (sourceColumns.length > 0) {
    for (const column of sourceColumns) {
      mapping[column.name] = isAddingToExistingDataset
        ? targetColumnIdByName.get(column.name) || ""
        : column.name;
    }
  } else {
    mapping.key = "";
  }

  return { mapping: { ...mapping } };
}

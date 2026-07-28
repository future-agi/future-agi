export const getMappingValue = (mapping, variablePath) => {
  if (!mapping || !variablePath) return null;

  const parts = variablePath.split(".");
  let current = mapping;

  for (const part of parts) {
    if (current && typeof current === "object" && part in current) {
      current = current[part];
    } else {
      return null;
    }
  }

  return current;
};

// Text columns the user can map variables to.
export const buildImportColumns = (datasetDetail) => {
  const columns = datasetDetail?.column_config ?? [];
  return columns
    .filter((col) => col.data_type === "text")
    .map((col) => ({ headerName: col.name, field: col.id }));
};

// Build { variableName: [cellValue, ...] } for the mapped columns.
export const buildVariableData = (mapping, datasetDetail, variables) => {
  const table = datasetDetail?.table ?? [];
  const variableData = {};

  variables.forEach((variableName) => {
    const columnId = getMappingValue(mapping, variableName);
    if (columnId) {
      variableData[variableName] = table.map((row) => {
        const cell = row[columnId];
        return cell?.cell_value ?? "";
      });
    }
  });

  return variableData;
};

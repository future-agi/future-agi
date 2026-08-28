export const getSyntheticDefaultValues = (editData) => {
  return {
    name: editData?.dataset?.name ?? "",
    description: editData?.dataset?.description ?? "",
    kb_id: editData?.kb_id ?? editData?.kbId ?? "",
    useCase: editData?.dataset?.objective ?? "",
    pattern: editData?.dataset?.patterns ?? "",
    rowNumber: editData?.num_rows ?? editData?.numRows ?? "",
    columns:
      Array.isArray(editData?.columns) && editData?.columns?.length > 0
        ? transformColumnPayload(editData?.columns)
        : [
            {
              name: "",
              data_type: "",
              property: [],
              description: "",
            },
          ],
  };
};

export const transformColumnPayload = (columns = []) => {
  if (columns.length === 0) return columns;
  return columns.map((col) => ({
    ...col,
    data_type: col?.data_type ?? col?.dataType,
    property: col?.property
      ? Object.entries(col.property).map(([type, value]) => ({
          type,
          value,
        }))
      : [],
  }));
};

const normalizeSyntheticColumnProperty = (property) => {
  if (Array.isArray(property)) {
    return property.reduce((normalized, entry) => {
      if (entry?.type) {
        normalized[entry.type] = entry.value;
      }
      return normalized;
    }, {});
  }

  return property && typeof property === "object" ? property : {};
};

export const buildSyntheticColumnPayload = (column, description) => ({
  name: column?.name,
  data_type: column?.data_type ?? column?.dataType,
  description: description ?? column?.description ?? "",
  property: normalizeSyntheticColumnProperty(column?.property),
});

export const getRequestedTraceAttributeKeys = (columns = []) =>
  Array.from(
    new Set(
      (columns || [])
        .filter(
          (column) =>
            column?.groupBy === "Custom Columns" && column?.isVisible !== false,
        )
        .map((column) => column?.id)
        .filter(Boolean),
    ),
  );

export const getTraceAttributeRequestKey = (columns = []) =>
  JSON.stringify(getRequestedTraceAttributeKeys(columns));

export const getTraceAttributeRequestParams = (columns = []) => {
  const key = getTraceAttributeRequestKey(columns);
  return key === "[]" ? {} : { attribute_keys: key };
};

import { NON_ATTRIBUTE_CUSTOM_COLUMN_IDS } from "./defaultColumns";

// The Skills column (SKILL-TRACES §7.1) is hydrated per page from the
// trace-skills read, never as a latest-value attribute key: that path keeps
// one value per key per trace and cannot show every skill a trace loaded.
const NON_ATTRIBUTE_IDS = new Set(NON_ATTRIBUTE_CUSTOM_COLUMN_IDS);

export const getRequestedTraceAttributeKeys = (columns = []) =>
  Array.from(
    new Set(
      (columns || [])
        .filter(
          (column) =>
            column?.groupBy === "Custom Columns" && column?.isVisible !== false,
        )
        .map((column) => column?.id)
        .filter((id) => Boolean(id) && !NON_ATTRIBUTE_IDS.has(id)),
    ),
  );

export const getTraceAttributeRequestKey = (columns = []) =>
  JSON.stringify(getRequestedTraceAttributeKeys(columns));

export const getTraceAttributeRequestParams = (columns = []) => {
  const key = getTraceAttributeRequestKey(columns);
  return key === "[]" ? {} : { attribute_keys: key };
};

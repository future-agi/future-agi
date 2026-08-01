import {
  extractAttributeFilters,
  getTaskFilterApiKey,
} from "../NewTaskDrawer/validation";

export const buildEvalTaskEditFilters = (data, startDate, endDate) => {
  const attributeFilters = extractAttributeFilters(data?.filters);
  const systemFilters = {};

  (data?.filters || []).forEach((filter) => {
    if (!filter?.property || filter.property === "attributes") return;
    const apiKey = getTaskFilterApiKey(filter.property);
    const value = filter?.filterConfig?.filterValue;
    const values = Array.isArray(value)
      ? value
      : value !== undefined && value !== null && value !== ""
        ? [value]
        : [];
    if (!values.length) return;
    if (systemFilters[apiKey]) {
      systemFilters[apiKey].push(...values);
    } else {
      systemFilters[apiKey] = [...values];
    }
  });

  return {
    project_id: data?.project,
    date_range: [
      new Date(startDate).toISOString(),
      new Date(endDate).toISOString(),
    ],
    ...systemFilters,
    ...(attributeFilters.length > 0 ? { filters: attributeFilters } : {}),
  };
};

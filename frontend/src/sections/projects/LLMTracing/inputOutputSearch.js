// This is one OR leaf, not two AND-connected input/output filters.
export const INPUT_OUTPUT_SEARCH_MAX_BYTES = 1024;

export const isInputOutputSearchFilter = (filter) =>
  filter?.column_id === "input_output" &&
  filter?.filter_config?.col_type === "SYSTEM_METRIC";

export const inputOutputSearchValue = (filters) =>
  filters?.find(isInputOutputSearchFilter)?.filter_config?.filter_value || "";

export const replaceInputOutputSearch = (filters, value) => {
  const others = (filters || []).filter((filter) => !isInputOutputSearchFilter(filter));
  if (!value.trim()) return others;
  return [
    ...others,
    {
      column_id: "input_output",
      display_name: "Input or output",
      filter_config: {
        col_type: "SYSTEM_METRIC",
        filter_type: "text",
        filter_op: "contains",
        filter_value: value,
      },
    },
  ];
};

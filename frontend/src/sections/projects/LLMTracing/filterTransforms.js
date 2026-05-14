export const panelFiltersToApiFilters = (filters = []) => {
  const opMap = {
    is: "equals",
    is_not: "not_equals",
    contains: "contains",
    not_contains: "not_contains",
    equals: "equals",
    equal_to: "equal_to",
    not_equal_to: "not_equal_to",
    greater_than: "greater_than",
    greater_than_or_equal: "greater_than_or_equal",
    less_than: "less_than",
    less_than_or_equal: "less_than_or_equal",
    between: "between",
    not_between: "not_between",
  };
  const typeMap = {
    string: "text",
    number: "number",
    boolean: "boolean",
    categorical: "categorical",
    thumbs: "thumbs",
    text: "text",
    annotator: "text",
  };
  const colTypeMap = {
    attribute: "SPAN_ATTRIBUTE",
    system: "SYSTEM_METRIC",
    eval: "EVAL_METRIC",
    annotation: "ANNOTATION",
  };

  return filters.map((filter) => {
    const baseOp = opMap[filter.operator] || filter.operator;
    let filterOp = baseOp;
    let filterValue = filter.value;
    if (Array.isArray(filterValue)) {
      if (filterValue.length === 1) {
        filterValue = filterValue[0];
      } else if (filterValue.length > 1) {
        if (baseOp === "equals") filterOp = "in";
        else if (baseOp === "not_equals") filterOp = "not_in";
        else filterValue = filterValue.join(",");
      }
    }

    const apiColType = filter.apiColType || colTypeMap[filter.fieldCategory];
    return {
      column_id: filter.field,
      ...(filter.fieldName && { display_name: filter.fieldName }),
      filter_config: {
        filter_type: typeMap[filter.fieldType] || "text",
        filter_op: filterOp,
        filter_value: filterValue,
        ...(apiColType && {
          col_type: apiColType,
        }),
      },
    };
  });
};

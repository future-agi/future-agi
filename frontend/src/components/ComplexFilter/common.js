import { format } from "date-fns";
import _ from "lodash";
import {
  FILTER_COLUMN_TYPES,
  FILTER_TYPE_ALLOWED_OPS,
  LIST_FILTER_OPS,
  NO_VALUE_FILTER_OPS,
  RANGE_FILTER_OPS,
} from "src/api/contracts/filter-contract.generated";
import { FilterTypeMapper } from "src/utils/constants";
import { formatISOCustom } from "src/utils/utils";
import { z } from "zod";

const AllowedOperators = Array.from(
  new Set(Object.values(FILTER_TYPE_ALLOWED_OPS).flat()),
);
const AllowedFilterTypes = Object.keys(FILTER_TYPE_ALLOWED_OPS);
const AllowedColumnTypes = FILTER_COLUMN_TYPES;
const ListOperators = new Set(LIST_FILTER_OPS);
const NoValueOperators = new Set(NO_VALUE_FILTER_OPS);
const RangeOperators = new Set(RANGE_FILTER_OPS);

export const stripUiFilterKeys = (filters = []) =>
  (Array.isArray(filters) ? filters : []).map((filter) => {
    if (!filter || typeof filter !== "object") return filter;
    const cleaned = { ...filter };
    delete cleaned._meta;
    delete cleaned.id;
    return cleaned;
  });

export const NULL_OPERATORS = ["is_null", "is_not_null"];

export const getComplexFilterValidation = (
  formatColId,
  getCustomProperties,
) => {
  return z
    .object({
      column_id: z
        .string()
        .min(1)
        .transform((val) => {
          return val;
        }),
      _meta: z
        .object({
          parentProperty: z.string().optional(),
        })
        .optional()
        .default({ parentProperty: "" }),
      filter_config: z
        .object({
          filter_op: z.enum(
            // @ts-ignore
            AllowedOperators,
          ),
          filter_type: z.enum(
            // @ts-ignore
            AllowedFilterTypes,
          ),
          filter_value: z
            .union([
              z.string(),
              z.number(),
              z.array(z.string()),
              z.array(z.any()),
              z.boolean(),
            ])
            .optional(),
          col_type: z
            .enum(
              // @ts-ignore
              AllowedColumnTypes,
            )
            .optional(),
        })
        .refine(
          (val) => {
            // Skip validation for null operators as they don't require filter_value
            if (NoValueOperators.has(val.filter_op)) {
              return true;
            }

            switch (val.filter_type) {
              case "number": {
                const values = Array.isArray(val.filter_value)
                  ? val.filter_value
                  : [val.filter_value];
                const hasValue = (item) =>
                  item !== "" && item !== null && item !== undefined;

                if (RangeOperators.has(val.filter_op)) {
                  if (values.length !== 2 || !values.every(hasValue))
                    return false;
                  return values.every(
                    (item) => !Number.isNaN(parseFloat(item)),
                  );
                }

                if (values.length === 0 || !hasValue(values[0])) return false;
                return !Number.isNaN(parseFloat(values[0]));
              }
              case "datetime": {
                const values = Array.isArray(val.filter_value)
                  ? val.filter_value
                  : [val.filter_value];
                const hasValue = (item) =>
                  item !== "" && item !== null && item !== undefined;

                if (RangeOperators.has(val.filter_op)) {
                  if (values.length !== 2 || !values.every(hasValue))
                    return false;
                  try {
                    format(new Date(values[0]), "yyyy-MM-dd HH:mm:ss");
                    format(new Date(values[1]), "yyyy-MM-dd HH:mm:ss");
                  } catch (error) {
                    return false;
                  }
                } else {
                  if (values.length === 0 || !hasValue(values[0])) return false;
                  try {
                    format(new Date(values[0]), "yyyy-MM-dd HH:mm:ss");
                  } catch (error) {
                    return false;
                  }
                }
                return true;
              }
              case "text":
              case "categorical":
              case "thumbs":
              case "annotator":
                if (ListOperators.has(val.filter_op)) {
                  return (
                    Array.isArray(val.filter_value) &&
                    val.filter_value.length > 0 &&
                    val.filter_value.every(
                      (item) => item !== "" && item != null,
                    )
                  );
                }
                if (Array.isArray(val.filter_value)) {
                  return (
                    val.filter_value.length > 0 &&
                    val.filter_value.every(
                      (item) => item !== "" && item != null,
                    )
                  );
                }
                return Boolean(
                  val.filter_value &&
                    typeof val.filter_value === "string" &&
                    val.filter_value.length > 0,
                );
              case "boolean":
                return typeof val.filter_value === "boolean";
              case "array":
                if (Array.isArray(val.filter_value)) {
                  return (
                    val.filter_value.length > 0 &&
                    val.filter_value.every(
                      (item) => item !== "" && item != null,
                    )
                  );
                }
                return val.filter_value !== "" && val.filter_value != null;
              default:
                return true;
            }
          },
          {
            message: "wrong filter",
          },
        ),
    })
    .transform((val) => {
      const isNullOperator = NoValueOperators.has(val.filter_config.filter_op);

      let finalFilters = {};
      if (isNullOperator) {
        finalFilters = {
          column_id: val.column_id,
          filter_config: {
            ...val.filter_config,
            filter_value: null,
          },
        };
      } else if (val.filter_config.filter_type === "number") {
        const values = Array.isArray(val.filter_config.filter_value)
          ? val.filter_config.filter_value
          : [val.filter_config.filter_value];
        let newFilterValues;
        if (RangeOperators.has(val.filter_config.filter_op)) {
          newFilterValues = values.map((item) => parseFloat(item));
        } else {
          newFilterValues = parseFloat(values[0]);
        }
        finalFilters = {
          column_id: val.column_id,
          filter_config: {
            ...val.filter_config,
            filter_value: newFilterValues,
          },
        };
      } else if (val.filter_config.filter_type === "datetime") {
        const values = Array.isArray(val.filter_config.filter_value)
          ? val.filter_config.filter_value
          : [val.filter_config.filter_value];
        let newFilterValues;
        if (RangeOperators.has(val.filter_config.filter_op)) {
          newFilterValues = values.map((item) =>
            formatISOCustom(new Date(item)),
          );
        } else {
          newFilterValues = formatISOCustom(new Date(values[0]));
        }
        finalFilters = {
          column_id: val.column_id,
          filter_config: {
            ...val.filter_config,
            filter_value: newFilterValues,
          },
        };
      } else {
        finalFilters = {
          column_id: val.column_id,
          filter_config: {
            ...val.filter_config,
          },
        };
      }

      if (getCustomProperties) {
        const customProps = getCustomProperties(val);
        return {
          ...finalFilters,
          ...customProps,
          filter_config: {
            ...finalFilters?.filter_config,
            ...(customProps?.col_type
              ? { col_type: customProps.col_type }
              : {}),
          },
        };
      } else {
        return finalFilters;
      }
    });
};

export const isEmptyFilter = (filter) => {
  const internalFilter = { ...filter };
  delete internalFilter.id;

  return _.isEqual(internalFilter, {
    column_id: "",
    filter_config: {
      filter_type: "",
      filter_op: "",
      filter_value: "",
    },
  });
};

export const handleNumericInput = (v) => {
  // Allow digits 0-9 and decimal point
  const value = v.replace(/[^0-9.]/g, "");
  // Ensure only one decimal point
  const parts = value.split(".");
  if (parts.length > 2) {
    return parts[0] + "." + parts.slice(1).join("");
  }
  return value;
};

export const avoidDuplicateFilterSet = (prev, filter) => {
  let filterAdded = false;
  const result = prev.reduce((acc, f) => {
    if (isEmptyFilter(f)) {
      return acc;
    }
    if (f.column_id === filter.column_id) {
      filterAdded = true;
      return [...acc, filter];
    }
    return [...acc, f];
  }, []);

  if (!filterAdded) {
    result.push(filter);
  }

  return result;
};

export const getFilterType = (filterDef) => {
  if (filterDef?.multiSelect && filterDef?.filterType?.type === "option") {
    return "array";
  }
  return FilterTypeMapper[filterDef.filterType.type];
};

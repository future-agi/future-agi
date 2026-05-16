import { format } from "date-fns";
import _ from "lodash";
import {
  FILTER_TYPE_ALLOWED_OPS,
  RANGE_FILTER_OPS,
} from "src/api/contracts/filter-contract.generated";
import { FilterTypeMapper } from "src/utils/constants";
import { formatISOCustom } from "src/utils/utils";
import { z } from "zod";

const AllowedOperators = Array.from(
  new Set(Object.values(FILTER_TYPE_ALLOWED_OPS).flat()),
);
const RangeOperators = new Set(RANGE_FILTER_OPS);

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
      _meta: z.object({
        parentProperty: z.string(),
      }),
      filter_config: z
        .object({
          filter_op: z.enum(
            // @ts-ignore
            AllowedOperators,
          ),
          filter_type: z.enum([
            "number",
            "text",
            "datetime",
            "boolean",
            "array",
          ]),
          filter_value: z
            .union([
              z.string(),
              z.array(z.string()),
              z.array(z.any()),
              z.boolean(),
            ])
            .optional(),
          col_type: z
            .enum(["SPAN_ATTRIBUTE", "ANNOTATION", "SYSTEM_METRIC"])
            .optional(),
        })
        .refine(
          (val) => {
            // Skip validation for null operators as they don't require filter_value
            if (
              val.filter_op === "is_null" ||
              val.filter_op === "is_not_null"
            ) {
              return true;
            }

            switch (val.filter_type) {
              case "number":
                if (!val.filter_value || !Array.isArray(val.filter_value))
                  return false;

                if (RangeOperators.has(val.filter_op)) {
                  if (val.filter_value.length !== 2) return false;
                  if (
                    val.filter_value[0].length === 0 ||
                    val.filter_value[1].length === 0
                  )
                    return false;
                  try {
                    parseFloat(val.filter_value[0]);
                    parseFloat(val.filter_value[1]);
                  } catch (error) {
                    return false;
                  }
                } else {
                  if (val.filter_value.length == 0) return false;
                  if (val.filter_value[0].length === 0) return false;
                  try {
                    parseFloat(val.filter_value[0]);
                  } catch (error) {
                    return false;
                  }
                }
                return true;
              case "datetime":
                if (!val.filter_value || !Array.isArray(val.filter_value))
                  return false;

                if (RangeOperators.has(val.filter_op)) {
                  if (val.filter_value.length !== 2) return false;
                  try {
                    format(
                      new Date(val.filter_value[0]),
                      "yyyy-MM-dd HH:mm:ss",
                    );
                    format(
                      new Date(val.filter_value[1]),
                      "yyyy-MM-dd HH:mm:ss",
                    );
                  } catch (error) {
                    return false;
                  }
                } else {
                  if (val.filter_value.length == 0) return false;
                  try {
                    format(
                      new Date(val.filter_value[0]),
                      "yyyy-MM-dd HH:mm:ss",
                    );
                  } catch (error) {
                    return false;
                  }
                }
                return true;
              case "text":
                return Boolean(
                  val.filter_value &&
                    typeof val.filter_value === "string" &&
                    val.filter_value.length > 0,
                );
              case "boolean":
                return typeof val.filter_value === "boolean";
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
      // For null operators, set filter_value to empty string even if it exists in old data
      const isNullOperator =
        val.filter_config.filter_op === "is_null" ||
        val.filter_config.filter_op === "is_not_null";

      let finalFilters = {};
      if (val.filter_config.filter_type === "number") {
        let newFilterValues;
        if (RangeOperators.has(val.filter_config.filter_op)) {
          newFilterValues = val.filter_config.filter_value.map((item) =>
            parseFloat(item),
          );
        } else {
          newFilterValues = parseFloat(val.filter_config.filter_value[0]);
        }
        finalFilters = {
          column_id: val.column_id,
          filter_config: {
            ...val.filter_config,
            filter_value: newFilterValues,
          },
        };
      } else if (val.filter_config.filter_type === "datetime") {
        let newFilterValues;
        if (RangeOperators.has(val.filter_config.filter_op)) {
          newFilterValues = val.filter_config.filter_value.map((item) =>
            formatISOCustom(new Date(item)),
          );
        } else {
          newFilterValues = formatISOCustom(
            new Date(val.filter_config.filter_value[0]),
          );
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
            ...(isNullOperator && { filter_value: "" }),
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

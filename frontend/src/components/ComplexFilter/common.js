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
      columnId: z
        .string()
        .min(1)
        .transform((val) => {
          return val;
        }),
      _meta: z.object({
        parentProperty: z.string(),
      }),
      filterConfig: z
        .object({
          filterOp: z.enum(
            // @ts-ignore
            AllowedOperators,
          ),
          filterType: z.enum([
            "number",
            "text",
            "datetime",
            "boolean",
            "array",
          ]),
          filterValue: z
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
            // Skip validation for null operators as they don't require filterValue
            if (val.filterOp === "is_null" || val.filterOp === "is_not_null") {
              return true;
            }

            switch (val.filterType) {
              case "number":
                if (!val.filterValue || !Array.isArray(val.filterValue))
                  return false;

                if (RangeOperators.has(val.filterOp)) {
                  if (val.filterValue.length !== 2) return false;
                  if (
                    val.filterValue[0].length === 0 ||
                    val.filterValue[1].length === 0
                  )
                    return false;
                  try {
                    parseFloat(val.filterValue[0]);
                    parseFloat(val.filterValue[1]);
                  } catch (error) {
                    return false;
                  }
                } else {
                  if (val.filterValue.length == 0) return false;
                  if (val.filterValue[0].length === 0) return false;
                  try {
                    parseFloat(val.filterValue[0]);
                  } catch (error) {
                    return false;
                  }
                }
                return true;
              case "datetime":
                if (!val.filterValue || !Array.isArray(val.filterValue))
                  return false;

                if (RangeOperators.has(val.filterOp)) {
                  if (val.filterValue.length !== 2) return false;
                  try {
                    format(new Date(val.filterValue[0]), "yyyy-MM-dd HH:mm:ss");
                    format(new Date(val.filterValue[1]), "yyyy-MM-dd HH:mm:ss");
                  } catch (error) {
                    return false;
                  }
                } else {
                  if (val.filterValue.length == 0) return false;
                  try {
                    format(new Date(val.filterValue[0]), "yyyy-MM-dd HH:mm:ss");
                  } catch (error) {
                    return false;
                  }
                }
                return true;
              case "text":
                return Boolean(
                  val.filterValue &&
                    typeof val.filterValue === "string" &&
                    val.filterValue.length > 0,
                );
              case "boolean":
                return typeof val.filterValue === "boolean";
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
      // For null operators, set filterValue to empty string even if it exists in old data
      const isNullOperator =
        val.filterConfig.filterOp === "is_null" ||
        val.filterConfig.filterOp === "is_not_null";

      let finalFilters = {};
      if (val.filterConfig.filterType === "number") {
        let newFilterValues;
        if (RangeOperators.has(val.filterConfig.filterOp)) {
          newFilterValues = val.filterConfig.filterValue.map((item) =>
            parseFloat(item),
          );
        } else {
          newFilterValues = parseFloat(val.filterConfig.filterValue[0]);
        }
        finalFilters = {
          columnId: val.columnId,
          filterConfig: {
            ...val.filterConfig,
            filterValue: newFilterValues,
          },
        };
      } else if (val.filterConfig.filterType === "datetime") {
        let newFilterValues;
        if (RangeOperators.has(val.filterConfig.filterOp)) {
          newFilterValues = val.filterConfig.filterValue.map((item) =>
            formatISOCustom(new Date(item)),
          );
        } else {
          newFilterValues = formatISOCustom(
            new Date(val.filterConfig.filterValue[0]),
          );
        }
        finalFilters = {
          columnId: val.columnId,
          filterConfig: { ...val.filterConfig, filterValue: newFilterValues },
        };
      } else {
        finalFilters = {
          columnId: val.columnId,
          filterConfig: {
            ...val.filterConfig,
            ...(isNullOperator && { filterValue: "" }),
          },
        };
      }

      if (getCustomProperties) {
        const customProps = getCustomProperties(val);
        return {
          ...finalFilters,
          ...customProps,
          filterConfig: {
            ...finalFilters?.filterConfig,
            ...(customProps?.colType ? { colType: customProps.colType } : {}),
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
    columnId: "",
    filterConfig: {
      filterType: "",
      filterOp: "",
      filterValue: "",
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
    if (f.columnId === filter.columnId) {
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

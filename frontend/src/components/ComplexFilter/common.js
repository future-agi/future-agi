import { format } from "date-fns";
import _ from "lodash";
import {
  FILTER_COLUMN_TYPES,
  FILTER_TYPE_ALLOWED_OPS,
  LIST_FILTER_OPS,
  NO_VALUE_FILTER_OPS,
  RANGE_FILTER_OPS,
  STRUCTURED_SPAN_ATTRIBUTE_ALLOWED_OPS,
} from "src/api/contracts/filter-contract.generated";
import { FilterTypeMapper } from "src/utils/constants";
import { formatISOCustom } from "src/utils/utils";
import { z } from "zod";

const AllowedOperators = Array.from(
  new Set(
    [
      ...Object.values(FILTER_TYPE_ALLOWED_OPS),
      ...Object.values(STRUCTURED_SPAN_ATTRIBUTE_ALLOWED_OPS),
    ].flat(),
  ),
);
const AllowedFilterTypes = Array.from(
  new Set([
    ...Object.keys(FILTER_TYPE_ALLOWED_OPS),
    ...Object.keys(STRUCTURED_SPAN_ATTRIBUTE_ALLOWED_OPS),
  ]),
);
const AllowedColumnTypes = FILTER_COLUMN_TYPES;
const ListOperators = new Set(LIST_FILTER_OPS);
const NoValueOperators = new Set(NO_VALUE_FILTER_OPS);
const RangeOperators = new Set(RANGE_FILTER_OPS);

export const stripUiFilterKeys = (filters = []) =>
  (Array.isArray(filters) ? filters : []).map((filter) => {
    if (!filter || typeof filter !== "object") return filter;
    const cleaned = { ...filter };
    if (cleaned.registryId && !cleaned.property_id) {
      cleaned.property_id = cleaned.registryId;
    }
    delete cleaned._meta;
    delete cleaned.id;
    delete cleaned.registryId;
    return cleaned;
  });

export const getFilterDefinitionIdentity = (definition) => ({
  column_id: definition?.propertyId,
  ...(definition?.registryId || definition?.property_id
    ? { registryId: definition.registryId || definition.property_id }
    : {}),
});

const filterRegistryId = (value) =>
  value?.registryId || value?.property_id || "";

const filterNativePropertyId = (value) =>
  value?.propertyId || value?.column_id || "";

// Registry identity is authoritative whenever both sides have one. Falling
// back to the native column is deliberate compatibility for filters saved
// before property_id existed.
export const filtersSharePropertyIdentity = (left, right) => {
  const leftRegistryId = filterRegistryId(left);
  const rightRegistryId = filterRegistryId(right);
  if (leftRegistryId && rightRegistryId) {
    return leftRegistryId === rightRegistryId;
  }
  const leftPropertyId = filterNativePropertyId(left);
  return (
    Boolean(leftPropertyId) && leftPropertyId === filterNativePropertyId(right)
  );
};

export const getFilterDefinitionSelectionValue = (definition) => {
  const registryId = filterRegistryId(definition);
  const nativePropertyId = filterNativePropertyId(definition);
  if (registryId) {
    // The native suffix remains part of UI selection identity because several
    // choices of one logical property can share a registry ID (annotation
    // dependents use `<label-id>**<choice>`). Registry ID still separates a
    // same-name system field from a custom attribute.
    return `registry:${JSON.stringify([registryId, nativePropertyId])}`;
  }
  return nativePropertyId || definition?.propertyName || "";
};

export const filterDefinitionMatchesSelection = (definition, selection) => {
  if (!selection) return false;
  if (getFilterDefinitionSelectionValue(definition) === selection) return true;
  // Explicit legacy fallback: old _meta paths stored propertyId/propertyName.
  return (
    filterRegistryId(definition) === selection ||
    definition?.propertyId === selection ||
    definition?.propertyName === selection
  );
};

const registryUsageKey = (registryId) => `registry:${registryId}`;
const legacyUsageKey = (columnId) => `legacy:${columnId}`;

export const getFilterUsageCounts = (filters = []) =>
  (filters || []).reduce((counts, filter) => {
    const registryId = filterRegistryId(filter);
    const columnId = filterNativePropertyId(filter);
    const key = registryId
      ? registryUsageKey(registryId)
      : columnId
        ? legacyUsageKey(columnId)
        : "";
    if (key) counts[key] = (counts[key] || 0) + 1;
    return counts;
  }, {});

export const getFilterDefinitionUsage = (counts, definition) => {
  const registryId = filterRegistryId(definition);
  const columnId = filterNativePropertyId(definition);
  const identifiedCount = registryId
    ? counts?.[registryUsageKey(registryId)] || 0
    : 0;
  // A row without property_id is inherently ambiguous. Count it by its native
  // column for compatibility, but never combine two identified definitions.
  const legacyCount = columnId ? counts?.[legacyUsageKey(columnId)] || 0 : 0;
  return identifiedCount + legacyCount;
};

export const isFilterDefinitionAtMaxUsage = (
  definition,
  counts,
  currentFilter,
) =>
  Boolean(
    definition?.maxUsage &&
      getFilterDefinitionUsage(counts, definition) >= definition.maxUsage &&
      !filtersSharePropertyIdentity(currentFilter, definition),
  );

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
      registryId: z.string().optional(),
      property_id: z.string().optional(),
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
              z.record(z.union([z.string(), z.number().finite(), z.boolean()])),
            ])
            .optional(),
          col_type: z
            .enum(
              // @ts-ignore
              AllowedColumnTypes,
            )
            .optional(),
          attribute_value_types: z
            .array(z.enum(["string", "number", "boolean"]).nullable())
            .optional(),
        })
        .refine(
          (val) => {
            if (val.attribute_value_types !== undefined) {
              if (
                val.col_type !== "SPAN_ATTRIBUTE" ||
                !ListOperators.has(val.filter_op) ||
                !Array.isArray(val.filter_value) ||
                val.attribute_value_types.length !== val.filter_value.length
              ) {
                return false;
              }
            }

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
              case "map":
                return (
                  val.filter_value !== null &&
                  typeof val.filter_value === "object" &&
                  !Array.isArray(val.filter_value) &&
                  Object.keys(val.filter_value).length > 0
                );
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

      const registryId = val.property_id || val.registryId;
      if (registryId) finalFilters.property_id = registryId;

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
    if (filtersSharePropertyIdentity(f, filter)) {
      // Rows can share a column_id, and replacing each one pushed `filter`
      // per match — duplicate chips, and removing one left the filter applied.
      if (filterAdded) {
        return acc;
      }
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

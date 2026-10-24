/**
 * Adapter between ComplexFilter state and the shared Filter panel.
 *
 * Screens keep their filter state exactly as ComplexFilter wrote it:
 *   [{ id, column_id, registryId?, filter_config: { filter_type, filter_op,
 *      filter_value, col_type? }, _meta: { parentProperty, ... } }]
 * together with a `filterDefinition` tree (propertyName, propertyId,
 * filterType, dependents ...). The panel works on flat rows
 * `{ field, operator, value }` over a field schema, so:
 *
 *   flattenFilterDefinition(defs)  -> leaves (one per selectable property)
 *   leavesToFields(leaves)         -> field schema for <FilterPanel fields />
 *   toPanelValue(filters, leaves)  -> panel rows for the current state
 *   fromPanelValue(rows, leaves)   -> ComplexFilter rows, shaped exactly as
 *                                     FilterRow + the value selectors wrote them
 *
 * Quick filters, validators and request builders downstream keep reading the
 * same state, so nothing but the editing surface changes.
 */
import { format } from "date-fns";
import {
  AdvanceNumberFilterOperators,
  FilterColTypes,
  FilterDefaultOperators,
  FilterDefaultValues,
  FilterTypeMapper,
} from "src/utils/constants";
import { getRandomId } from "src/utils/utils";
import {
  getFilterDefinitionIdentity,
  getFilterDefinitionSelectionValue,
  getFilterType,
  isEmptyFilter,
  NULL_OPERATORS,
} from "src/components/ComplexFilter/common";

const NUMBER_OPERATORS = AdvanceNumberFilterOperators.map((op) => op.value);
const RANGE_OPERATORS = new Set(["between", "not_between"]);
const NULL_OPS = new Set(NULL_OPERATORS);
const DATE_INPUT_FORMAT = "yyyy-MM-dd'T'HH:mm";

// The panel's text editor works on `in` / `not_in` lists; ComplexFilter's text
// rows carry `equals` / `not_equals` with a single string.
const TO_PANEL_TEXT_OP = { equals: "in", not_equals: "not_in" };

const isBlank = (value) =>
  value === undefined ||
  value === null ||
  value === "" ||
  (Array.isArray(value) && value.length === 0);

const asString = (value) =>
  value === undefined || value === null ? "" : String(value);

const asArray = (value) => {
  if (Array.isArray(value)) return value;
  return isBlank(value) ? [] : [value];
};

const formatDateInput = (value) => {
  if (isBlank(value)) return "";
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? "" : format(date, DATE_INPUT_FORMAT);
};

const parseDateInput = (value) => {
  if (isBlank(value)) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date;
};

const optionList = (options = []) =>
  options.map((option) =>
    typeof option === "object" && option !== null
      ? { value: option.value, label: option.label ?? String(option.value) }
      : { value: option, label: String(option) },
  );

// ---------------------------------------------------------------------------
// Definition tree -> leaves
// ---------------------------------------------------------------------------

const isPreset = (def) =>
  Boolean(def?.hideValueSelector) && !def?.dependents?.length;

const hasDependents = (def) => Boolean(def?.dependents?.length);

const selectionOf = getFilterDefinitionSelectionValue;

// Custom span attributes: values come from the project's attribute catalog and
// carry a storage type per value, which the panel's attribute picker handles.
export const ATTRIBUTE_CATEGORY = "attribute";
const isAttributeDef = (def, chain) =>
  Boolean(def?.allowTypeChange) &&
  chain.length > 1 &&
  FilterColTypes[chain[0]?.propertyName] === "SPAN_ATTRIBUTE";

const kindOf = (def) => {
  const type = def?.filterType?.type;
  if (type === "number")
    return def.filterType.options ? "numberOptions" : "number";
  if (type === "option") return def.multiSelect ? "optionMulti" : "option";
  if (type === "text") return "text";
  if (type === "date") return "date";
  if (type === "boolean") return "boolean";
  return "text";
};

const overrideOps = (def) =>
  Array.isArray(def?.overrideOperators) && def.overrideOperators.length
    ? def.overrideOperators.map((op) => op.value)
    : null;

const panelOperators = (leaf) => {
  const { def, kind } = leaf;
  const override = overrideOps(def);
  switch (kind) {
    case "number":
    case "date":
      return override || NUMBER_OPERATORS;
    case "numberOptions":
    case "option":
    case "preset":
    case "presetGroup":
      return ["equals"];
    case "optionMulti":
      return ["contains"];
    case "boolean":
      return override || ["equals"];
    case "text":
    default: {
      const ops = override || [def?.defaultFilter ?? "equals"];
      return ops.map((op) => TO_PANEL_TEXT_OP[op] || op);
    }
  }
};

/**
 * Walks a ComplexFilter `filterDefinition` into selectable leaves. A group
 * whose dependents are all value-less presets (annotation choices) becomes one
 * choice field; a lone preset becomes a single "Yes" choice.
 */
export const flattenFilterDefinition = (definitions = []) => {
  const leaves = [];
  const usedIds = new Set();

  const uniqueId = (def) => {
    let id = def.propertyId || def.registryId || def.propertyName || "field";
    if (usedIds.has(id)) id = selectionOf(def) || id;
    let candidate = id;
    let n = 2;
    while (usedIds.has(candidate)) {
      candidate = `${id}#${n}`;
      n += 1;
    }
    usedIds.add(candidate);
    return candidate;
  };

  const categoryOf = (chain) =>
    chain.length > 1 ? chain[0].propertyName : "General";

  const labelOf = (chain) => chain[chain.length - 1].propertyName;

  const walk = (defs, parents) => {
    (defs || []).forEach((def) => {
      if (!def) return;
      const chain = [...parents, def];
      if (hasDependents(def)) {
        const children = def.dependents;
        if (children.length && children.every(isPreset)) {
          leaves.push({
            id: uniqueId(def),
            label: labelOf(chain),
            category: categoryOf(chain),
            kind: "presetGroup",
            def,
            chain,
            children: children.map((child) => ({
              def: child,
              chain: [...chain, child],
            })),
          });
          return;
        }
        walk(children, chain);
        return;
      }
      if (!def.propertyId && !def.registryId) return;
      // No value editor in the old filter either: nothing to filter on.
      if (!def.filterType?.type) return;
      if (isPreset(def)) {
        leaves.push({
          id: uniqueId(def),
          label: labelOf(chain),
          category: categoryOf(chain),
          kind: "preset",
          def,
          chain,
        });
        return;
      }
      const isAttribute = isAttributeDef(def, chain);
      // A known storage type uses the typed value picker; only attributes of
      // unknown type let the user choose how the value is read, and those
      // rows follow whichever type is picked.
      const typeSelectable = Boolean(def.allowTypeChange) && !def.asyncOptions;
      leaves.push({
        id: uniqueId(def),
        label: labelOf(chain),
        category: isAttribute ? ATTRIBUTE_CATEGORY : categoryOf(chain),
        kind: typeSelectable ? "text" : kindOf(def),
        def,
        chain,
        typeSelectable,
        isAttribute,
      });
    });
  };

  walk(definitions, []);

  // Two properties with one name (a column in two groups) get their group
  // prefixed so the picker can tell them apart.
  const nameCounts = leaves.reduce((counts, leaf) => {
    counts[leaf.label] = (counts[leaf.label] || 0) + 1;
    return counts;
  }, {});
  leaves.forEach((leaf) => {
    if (nameCounts[leaf.label] > 1 && leaf.chain.length > 1) {
      leaf.label = `${leaf.chain[leaf.chain.length - 2].propertyName} · ${leaf.label}`;
    }
  });
  return leaves;
};

const filterRegistryId = (value) =>
  value?.registryId || value?.property_id || "";

const rowMatchesDef = (row, def) => {
  if (!row?.column_id || row.column_id !== def?.propertyId) return false;
  const rowRegistry = filterRegistryId(row);
  const defRegistry = def?.registryId || def?.property_id || "";
  return !(rowRegistry && defRegistry && rowRegistry !== defRegistry);
};

// Returns { leaf, child? } for the leaf (or preset child) a stored row edits.
const findLeafForRow = (row, leaves) => {
  const parent = row?._meta?.parentProperty;
  const candidates = [];
  leaves.forEach((leaf) => {
    if (leaf.kind === "presetGroup") {
      leaf.children.forEach((child) => {
        if (rowMatchesDef(row, child.def)) candidates.push({ leaf, child });
      });
    } else if (rowMatchesDef(row, leaf.def)) {
      candidates.push({ leaf });
    }
  });
  if (candidates.length > 1 && parent) {
    const exact = candidates.find(
      ({ leaf }) => selectionOf(leaf.chain[0]) === parent,
    );
    if (exact) return exact;
  }
  return candidates[0] || null;
};

const syntheticKind = (filterType) => {
  switch (filterType) {
    case "number":
      return "number";
    case "datetime":
      return "date";
    case "boolean":
      return "boolean";
    default:
      return "text";
  }
};

/**
 * Leaves for the definition plus one per stored row that the definition no
 * longer lists (an attribute outside the loaded page, a column that was
 * removed). They keep such rows visible and removable in the panel instead of
 * leaving a hidden filter applied.
 */
export const buildFilterLeaves = (definitions, filters = []) => {
  const leaves = flattenFilterDefinition(definitions);
  const usedIds = new Set(leaves.map((leaf) => leaf.id));
  (filters || []).forEach((row) => {
    if (!row?.column_id || isEmptyFilter(row)) return;
    if (findLeafForRow(row, leaves)) return;
    let id = row.column_id;
    while (usedIds.has(id)) id = `${id}#legacy`;
    usedIds.add(id);
    const kind = syntheticKind(row.filter_config?.filter_type);
    const def = {
      propertyName: row.column_id,
      propertyId: row.column_id,
      ...(filterRegistryId(row) ? { registryId: filterRegistryId(row) } : {}),
      filterType: {
        type: kind === "date" ? "date" : kind === "text" ? "text" : kind,
      },
    };
    leaves.push({
      id,
      label: row.column_id,
      category: "General",
      kind,
      def,
      chain: [def],
      synthetic: row,
    });
  });
  return leaves;
};

const PANEL_FIELD_TYPE = {
  number: "number",
  numberOptions: "enum",
  option: "enum",
  optionMulti: "array",
  preset: "enum",
  presetGroup: "enum",
  text: "text",
  date: "date",
  boolean: "boolean",
};

// Typed attribute values were sent as a list (with a storage type per value).
const acceptsValueList = (leaf) =>
  Boolean(leaf.isAttribute && !leaf.typeSelectable);

export const leavesToFields = (leaves = []) =>
  leaves.map((leaf) => {
    const { kind, def } = leaf;
    let options;
    if (
      kind === "numberOptions" ||
      kind === "option" ||
      kind === "optionMulti"
    ) {
      options = optionList(def.filterType.options);
    } else if (kind === "presetGroup") {
      options = leaf.children.map(({ def: child }) => ({
        value: child.propertyId,
        label: child.propertyName,
      }));
    } else if (kind === "preset") {
      options = [{ value: "yes", label: "Yes" }];
    }
    return {
      id: leaf.id,
      label: leaf.label,
      category: leaf.category,
      type: PANEL_FIELD_TYPE[kind] || "text",
      ...(options ? { options } : {}),
      // These filters sent one string; only the typed attribute picker sent a
      // list, so everything else stays single-valued.
      ...(kind === "text" && !acceptsValueList(leaf)
        ? { multiple: false }
        : {}),
      ...(leaf.typeSelectable
        ? { typeSelectable: true }
        : leaf.isAttribute
          ? {}
          : { operators: panelOperators(leaf) }),
      ...(def.registryId ? { registryId: def.registryId } : {}),
      ...(leaf.isAttribute
        ? {
            apiColType: "SPAN_ATTRIBUTE",
            attributeTypes: def.attributeTypes,
            attributeTypesExact: def.attributeTypesExact,
          }
        : {}),
    };
  });

// ---------------------------------------------------------------------------
// ComplexFilter state -> panel rows
// ---------------------------------------------------------------------------

const panelRowBase = (leaf, field) => ({
  field: leaf.id,
  fieldName: leaf.label,
  fieldCategory: leaf.category,
  fieldType: field,
  ...(leaf.isAttribute
    ? {
        apiColType: "SPAN_ATTRIBUTE",
        ...(leaf.def.registryId ? { registryId: leaf.def.registryId } : {}),
      }
    : {}),
});

const rowFromFilter = (leaf, filter, child) => {
  const config = filter.filter_config || {};
  const op = config.filter_op || FilterDefaultOperators.text;
  const value = config.filter_value;
  const base = panelRowBase(leaf, PANEL_FIELD_TYPE[leaf.kind] || "text");

  switch (leaf.kind) {
    case "presetGroup":
      return {
        ...base,
        fieldType: "categorical",
        operator: "equals",
        value: [child.def.propertyId],
      };
    case "preset":
      return {
        ...base,
        fieldType: "categorical",
        operator: "equals",
        value: ["yes"],
      };
    case "number": {
      const values = asArray(value);
      if (NULL_OPS.has(op))
        return { ...base, fieldType: "number", operator: op, value: "" };
      return {
        ...base,
        fieldType: "number",
        operator: op,
        value: RANGE_OPERATORS.has(op)
          ? [asString(values[0]), asString(values[1])]
          : asString(values[0]),
      };
    }
    case "numberOptions": {
      const first = asArray(value)[0];
      return {
        ...base,
        fieldType: "categorical",
        operator: "equals",
        value: isBlank(first) ? [] : [first],
      };
    }
    case "option": {
      const first = asArray(value)[0];
      return {
        ...base,
        fieldType: "categorical",
        operator: "equals",
        value: isBlank(first) ? [] : [first],
      };
    }
    case "optionMulti": {
      const values =
        typeof value === "string"
          ? value.split(",").filter(Boolean)
          : asArray(value);
      return {
        ...base,
        fieldType: "array",
        operator: "contains",
        value: values,
      };
    }
    case "date": {
      if (NULL_OPS.has(op))
        return { ...base, fieldType: "date", operator: op, value: "" };
      const values = asArray(value);
      return {
        ...base,
        fieldType: "date",
        operator: op,
        value: RANGE_OPERATORS.has(op)
          ? [formatDateInput(values[0]), formatDateInput(values[1])]
          : formatDateInput(values[0]),
      };
    }
    case "boolean": {
      if (NULL_OPS.has(op))
        return { ...base, fieldType: "boolean", operator: op, value: "" };
      if (
        value !== true &&
        value !== false &&
        value !== "true" &&
        value !== "false"
      ) {
        return null;
      }
      return {
        ...base,
        fieldType: "boolean",
        operator: op,
        value: value === true || value === "true" ? "true" : "false",
      };
    }
    case "text":
    default: {
      const type = FilterTypeMapper[config.filter_type] || "text";
      const panelType = leaf.typeSelectable
        ? type === "number"
          ? "number"
          : type === "boolean"
            ? "boolean"
            : "string"
        : "string";
      if (panelType === "number") {
        const values = asArray(value);
        return {
          ...base,
          fieldType: "number",
          operator: op,
          value: RANGE_OPERATORS.has(op)
            ? [asString(values[0]), asString(values[1])]
            : asString(values[0]),
        };
      }
      if (panelType === "boolean") {
        return {
          ...base,
          fieldType: "boolean",
          operator: op,
          value: value === true || value === "true" ? "true" : "false",
        };
      }
      const textType = leaf.typeSelectable ? "text" : "string";
      if (NULL_OPS.has(op)) {
        return { ...base, fieldType: textType, operator: op, value: [] };
      }
      return {
        ...base,
        fieldType: textType,
        operator: TO_PANEL_TEXT_OP[op] || op,
        value: asArray(value).map(String),
        ...(Array.isArray(config.attribute_value_types)
          ? { valueTypes: config.attribute_value_types }
          : {}),
      };
    }
  }
};

export const toPanelValue = (filters = [], leaves = []) => {
  const rows = [];
  (filters || []).forEach((filter) => {
    if (!filter?.column_id || isEmptyFilter(filter)) return;
    const match = findLeafForRow(filter, leaves);
    if (!match) return;
    const row = rowFromFilter(match.leaf, filter, match.child);
    if (row) rows.push(row);
  });
  return rows;
};

// ---------------------------------------------------------------------------
// Panel rows -> ComplexFilter state
// ---------------------------------------------------------------------------

const metaFor = (chain) => {
  const meta = { parentProperty: selectionOf(chain[0]) };
  for (let i = 0; i < chain.length - 1; i += 1) {
    meta[selectionOf(chain[i])] = selectionOf(chain[i + 1]);
  }
  return meta;
};

const colTypeFor = (chain) => {
  const owner = chain.length > 1 ? chain[chain.length - 2] : chain[0];
  return FilterColTypes[owner?.propertyName];
};

const buildFilter = (def, chain, filterConfig, extra = {}) => {
  const colType = colTypeFor(chain);
  return {
    id: getRandomId(),
    ...getFilterDefinitionIdentity(def),
    filter_config: {
      ...filterConfig,
      ...(colType ? { col_type: colType } : {}),
    },
    _meta: metaFor(chain),
    ...extra,
  };
};

const firstValue = (value) => (Array.isArray(value) ? value[0] : value);

const textConfig = (op, value, { valueTypes, single = false } = {}) => {
  const filterType = "text";
  const all = asArray(value).filter((item) => !isBlank(item));
  const values = single ? all.slice(0, 1) : all;
  if (NULL_OPS.has(op)) {
    return { filter_type: filterType, filter_op: op, filter_value: "" };
  }
  if (op === "in" || op === "not_in") {
    // Attribute values keep their storage type per value, which only the list
    // operators can carry.
    if (
      Array.isArray(valueTypes) &&
      valueTypes.length === values.length &&
      values.length
    ) {
      return {
        filter_type: filterType,
        filter_op: op,
        filter_value: values,
        attribute_value_types: valueTypes,
      };
    }
    if (values.length === 1) {
      return {
        filter_type: filterType,
        filter_op: op === "in" ? "equals" : "not_equals",
        filter_value: String(values[0]),
      };
    }
    return {
      filter_type: filterType,
      filter_op: op,
      filter_value: values.map(String),
    };
  }
  return {
    filter_type: filterType,
    filter_op: op,
    filter_value: values.length ? String(values[0]) : "",
  };
};

const numberConfig = (op, value) => {
  if (NULL_OPS.has(op)) {
    return {
      filter_type: "number",
      filter_op: op,
      filter_value: FilterDefaultValues.number,
    };
  }
  if (RANGE_OPERATORS.has(op)) {
    const values = asArray(value);
    return {
      filter_type: "number",
      filter_op: op,
      filter_value: [asString(values[0]), asString(values[1])],
    };
  }
  return {
    filter_type: "number",
    filter_op: op,
    filter_value: [asString(firstValue(value)), ""],
  };
};

const filterFromRow = (leaf, row) => {
  const { def, chain, kind } = leaf;
  const op = row.operator;
  const value = row.value;

  switch (kind) {
    case "presetGroup": {
      const choice = asArray(value)[0];
      const child = leaf.children.find(
        ({ def: item }) => item.propertyId === choice,
      );
      if (!child) return null;
      return buildFilter(child.def, child.chain, {
        filter_type: getFilterType(child.def),
        filter_op:
          child.def.defaultFilter ??
          FilterDefaultOperators[child.def.filterType.type],
        filter_value:
          child.def.defaultFilterValue ??
          FilterDefaultValues[child.def.filterType.type],
      });
    }
    case "preset":
      return buildFilter(def, chain, {
        filter_type: getFilterType(def),
        filter_op:
          def.defaultFilter ?? FilterDefaultOperators[def.filterType.type],
        filter_value:
          def.defaultFilterValue ?? FilterDefaultValues[def.filterType.type],
      });
    case "number":
      return buildFilter(def, chain, numberConfig(op, value));
    case "numberOptions": {
      const choice = asArray(value)[0];
      if (isBlank(choice)) return null;
      return buildFilter(def, chain, {
        filter_type: "number",
        filter_op: "equals",
        filter_value: [choice, ""],
      });
    }
    case "option": {
      const choice = asArray(value)[0];
      if (isBlank(choice)) return null;
      return buildFilter(def, chain, {
        filter_type: getFilterType(def),
        filter_op: "equals",
        filter_value: choice,
      });
    }
    case "optionMulti": {
      const values = asArray(value).filter((item) => !isBlank(item));
      if (!values.length) return null;
      return buildFilter(def, chain, {
        filter_type: getFilterType(def),
        filter_op: "contains",
        filter_value: values,
      });
    }
    case "date": {
      if (NULL_OPS.has(op)) {
        return buildFilter(def, chain, {
          filter_type: "datetime",
          filter_op: op,
          filter_value: "",
        });
      }
      const values = asArray(value);
      return buildFilter(def, chain, {
        filter_type: "datetime",
        filter_op: op,
        filter_value: RANGE_OPERATORS.has(op)
          ? [parseDateInput(values[0]), parseDateInput(values[1])]
          : [parseDateInput(values[0]), ""],
      });
    }
    case "boolean": {
      if (NULL_OPS.has(op)) {
        return buildFilter(def, chain, {
          filter_type: "boolean",
          filter_op: op,
          filter_value: null,
        });
      }
      return buildFilter(def, chain, {
        filter_type: "boolean",
        filter_op: op,
        filter_value: value === true || value === "true",
      });
    }
    case "text":
    default: {
      if (leaf.typeSelectable) {
        if (row.fieldType === "number")
          return buildFilter(def, chain, numberConfig(op, value));
        if (row.fieldType === "boolean") {
          return buildFilter(def, chain, {
            filter_type: "boolean",
            filter_op: op,
            filter_value: value === true || value === "true",
          });
        }
      }
      return buildFilter(
        def,
        chain,
        textConfig(op, value, {
          valueTypes: leaf.isAttribute ? row.valueTypes : undefined,
          single: !acceptsValueList(leaf),
        }),
      );
    }
  }
};

// A synthetic leaf keeps the stored row's own metadata when it is rebuilt.
const withSyntheticMeta = (leaf, filter) => {
  if (!leaf.synthetic || !filter) return filter;
  const {
    _meta,
    filter_config: original,
    property_id: propertyId,
    registryId,
  } = leaf.synthetic;
  return {
    ...filter,
    ...(_meta ? { _meta } : {}),
    ...(propertyId ? { property_id: propertyId } : {}),
    ...(registryId ? { registryId } : {}),
    filter_config: {
      ...filter.filter_config,
      ...(original?.col_type ? { col_type: original.col_type } : {}),
    },
  };
};

// The panel can find an attribute the definition has not loaded yet (exact
// lookup by name). Such rows are written the way a loaded attribute would be.
const unknownAttributeLeaf = (row) => {
  if (!row?.field || row.fieldCategory !== ATTRIBUTE_CATEGORY) return null;
  const def = {
    propertyName: row.field,
    propertyId: row.field,
    ...(row.registryId ? { registryId: row.registryId } : {}),
    allowTypeChange: true,
    filterType: { type: "text" },
  };
  const group = { propertyName: "Attribute" };
  return {
    id: row.field,
    label: row.field,
    category: ATTRIBUTE_CATEGORY,
    kind: "text",
    def,
    chain: [group, def],
    typeSelectable: true,
    isAttribute: true,
  };
};

export const fromPanelValue = (rows, leaves = [], { defaultFilter } = {}) => {
  const byId = new Map(leaves.map((leaf) => [leaf.id, leaf]));
  const filters = [];
  (rows || []).forEach((row) => {
    const leaf = byId.get(row?.field) || unknownAttributeLeaf(row);
    if (!leaf) return;
    const filter = withSyntheticMeta(leaf, filterFromRow(leaf, row));
    if (filter) filters.push(filter);
  });
  if (!filters.length && defaultFilter) {
    return [{ ...defaultFilter, id: getRandomId() }];
  }
  return filters;
};

import { FIELD_TYPE_ALIASES } from "src/api/contracts/filter-contract.generated";
import { AllowedEvalSpanTypes } from "src/utils/constant";
import { getRandomId } from "src/utils/utils";

export const OBSERVATION_TYPE_FIELD = "observation_type";

// The form stores one row per selected span type; the panel shows them as a
// single multi-value row.
export const SPAN_TYPE_PROPERTY = {
  id: OBSERVATION_TYPE_FIELD,
  name: "Span Type",
  category: "system",
  rawCategory: "system_metric",
  // Not categorical: the panel drives multi-select off the operator and every
  // categorical operator is single-value. Not "text" either — that renders a
  // free-text box. "string" keeps the choices picker and carries `in`, the
  // only operator the API can express: it takes a value list and applies
  // `observation_type IN (...)`, with nowhere to put an operator.
  type: "string",
  operators: ["in"],
  choices: AllowedEvalSpanTypes.map((t) => t.value),
  choiceLabels: Object.fromEntries(
    AllowedEvalSpanTypes.map((t) => [t.value, t.label]),
  ),
};

export const CATEGORIES = [
  { key: "all", label: "All" },
  { key: "system", label: "Span" },
  { key: "attribute", label: "Attributes" },
];

// The contract is the authority on how a stored type spells itself — the
// backend rejects anything outside it — so normalise through its aliases
// rather than a local map that only knew number/boolean/text.
export const toPanelType = (filterType) =>
  FIELD_TYPE_ALIASES[filterType] ?? filterType ?? "text";

// Span attributes only ever store text, number or boolean; anything the
// contract normalises to something else is left alone so it round-trips
// instead of being rewritten as text.
const SPAN_ATTRIBUTE_TYPES = new Set(["text", "number", "boolean"]);

const toFilterType = (panelType) =>
  SPAN_ATTRIBUTE_TYPES.has(panelType) ? panelType : "text";

// The panel's numeric input is a plain text field, so an edited row comes back
// as a string. The old form coerced with parseFloat before storing, so keep
// doing that or an edited row changes the type of filter_value on save.
// Anything not a finite number — "", "-", a half-typed "1e" — is left alone.
const toApiNumber = (value) => {
  if (Array.isArray(value)) return value.map(toApiNumber);
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (trimmed === "" || !Number.isFinite(Number(trimmed))) return value;
  return Number(trimmed);
};

// The panel's boolean control works in the strings "true"/"false"; the API
// takes a native bool and drops the condition outright if given anything else.
const toPanelBool = (value) =>
  typeof value === "boolean" ? String(value) : value;
const toApiBool = (value) =>
  value === "true" ? true : value === "false" ? false : value;

/**
 * Form rows → panel rows.
 *
 * Row `fieldType` comes from the row's *stored* `filterType`, never from the
 * attribute's discovered type: a stored type that disagrees is preserved so
 * an untouched alert re-saves byte-identically.
 */
export const toPanelRows = (formRows = []) => {
  const rows = [];
  const spanTypes = [];

  formRows.forEach((row) => {
    if (row?.property === "observationType") {
      const value = row?.filterConfig?.filterValue;
      if (value) spanTypes.push(value);
      return;
    }
    if (row?.property !== "attributes" || !row?.propertyId) return;

    rows.push({
      field: row.propertyId,
      fieldName: row.propertyId,
      fieldCategory: "attribute",
      fieldType: toPanelType(row?.filterConfig?.filterType),
      apiColType: "SPAN_ATTRIBUTE",
      operator: row?.filterConfig?.filterOp || "equals",
      value:
        row?.filterConfig?.filterType === "boolean"
          ? toPanelBool(row?.filterConfig?.filterValue)
          : row?.filterConfig?.filterValue ?? "",
    });
  });

  if (spanTypes.length > 0) {
    rows.unshift({
      field: OBSERVATION_TYPE_FIELD,
      fieldName: "Span Type",
      fieldCategory: "system",
      fieldType: "string",
      operator: "in",
      value: spanTypes,
    });
  }

  return rows;
};

const LEGACY_SCALAR_OP = { in: "equals", not_in: "not_equals" };

/**
 * The panel has no scalar `equals` for string fields — it stores `in` and
 * labels it "equals", so hydrating a saved row rewrites the operator and wraps
 * the value. Map a single value back to the scalar form the API has always
 * stored; leave genuinely multi-value rows as `in`, the only shape that isn't
 * lossy.
 */
const toLegacyScalarOp = (operator, value) => {
  if (!LEGACY_SCALAR_OP[operator]) return { operator, value };

  const values = Array.isArray(value) ? value : [value];
  if (values.length > 1) return { operator, value };

  return { operator: LEGACY_SCALAR_OP[operator], value: values[0] ?? "" };
};

const API_VALUE_BY_TYPE = {
  boolean: toApiBool,
  number: toApiNumber,
};

/** Panel rows → form rows, matching what `transformFilterResponse` produces. */
export const toFormRows = (panelRows = []) => {
  const out = [];

  panelRows.forEach((row) => {
    if (!row?.field) return;

    if (row.field === OBSERVATION_TYPE_FIELD) {
      const values = Array.isArray(row.value)
        ? row.value
        : [row.value].filter(Boolean);
      values.forEach((value) => {
        out.push({
          id: getRandomId(),
          propertyId: "",
          property: "observationType",
          filterConfig: {
            filterType: "text",
            filterOp: "equals",
            filterValue: value,
          },
        });
      });
      return;
    }

    const { operator, value } = toLegacyScalarOp(
      row.operator || "equals",
      row.value,
    );

    const filterType = toFilterType(row.fieldType);

    out.push({
      id: getRandomId(),
      propertyId: row.field,
      property: "attributes",
      filterConfig: {
        filterType,
        filterOp: operator,
        filterValue: API_VALUE_BY_TYPE[filterType]?.(value) ?? value,
      },
    });
  });

  return out;
};

// ---------------------------------------------------------------------------
// Eval library filter: the shared Filter panel over the eval list's fields.
// State stays the API-shaped object EvalsListView and EvalPickerList read:
//   { search, owner, template_type, eval_type[], output_type[], tags[] }
// ---------------------------------------------------------------------------
export const EVAL_FILTER_FIELDS = [
  {
    id: "name",
    label: "Name",
    type: "text",
    operators: ["contains"],
    multiple: false,
  },
  {
    id: "eval_type",
    label: "Eval Type",
    type: "enum",
    multiple: true,
    options: ["llm", "code", "agent"],
    operators: ["in"],
  },
  {
    id: "output_type",
    label: "Output Type",
    type: "enum",
    multiple: true,
    options: ["pass_fail", "percentage", "deterministic"],
    operators: ["in"],
  },
  {
    id: "template_type",
    label: "Type",
    type: "enum",
    options: ["single", "composite"],
    operators: ["equals"],
  },
  {
    id: "owner",
    label: "Owner",
    type: "enum",
    options: ["user", "system"],
    operators: ["equals"],
  },
  { id: "tags", label: "Tags", type: "text", operators: ["in"] },
];

const asList = (value) => {
  if (Array.isArray(value)) return value;
  return value === undefined || value === null || value === "" ? [] : [value];
};

/** API-shaped filters -> panel rows. */
export const filtersToRows = (filters) => {
  const rows = [];
  const push = (field, fieldType, operator, value) => {
    if (value.length) rows.push({ field, fieldType, operator, value });
  };
  push("name", "string", "contains", asList(filters?.search));
  push("eval_type", "string", "in", asList(filters?.eval_type));
  push("output_type", "string", "in", asList(filters?.output_type));
  push(
    "template_type",
    "categorical",
    "equals",
    asList(filters?.template_type),
  );
  push("owner", "categorical", "equals", asList(filters?.owner));
  push("tags", "string", "in", asList(filters?.tags));
  return rows;
};

/** Panel rows -> API-shaped filters, or null when nothing is set. */
export const rowsToFilters = (rows) => {
  const result = {};
  const evalTypes = [];
  const outputTypes = [];

  for (const row of rows || []) {
    const values = asList(row.value).filter((v) => v !== "");
    if (!values.length) continue;

    if (row.field === "eval_type") {
      if (row.operator === "in" || row.operator === "equals")
        evalTypes.push(...values);
    } else if (row.field === "output_type") {
      if (row.operator === "in" || row.operator === "equals")
        outputTypes.push(...values);
    } else if (row.field === "owner") {
      result.owner = values[0];
    } else if (row.field === "template_type") {
      result.template_type = values[0];
    } else if (row.field === "name") {
      result.search = values[0];
    } else if (row.field === "tags") {
      if (!result.tags) result.tags = [];
      // Tags typed as a comma-separated string split into individual tags.
      result.tags.push(
        ...values.flatMap((v) =>
          typeof v === "string"
            ? v
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean)
            : [v],
        ),
      );
    }
  }

  if (evalTypes.length) result.eval_type = evalTypes;
  if (outputTypes.length) result.output_type = outputTypes;

  return Object.keys(result).length > 0 ? result : null;
};

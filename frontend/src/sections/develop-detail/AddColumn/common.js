import { getRandomId } from "src/utils/utils";
import {
  EXPRESSION_COLUMN_OPTION,
  expressionColumnOffered,
} from "./Expression/expressionColumnModel";

export const StaticColumns = [
  {
    label: "Text",
    icon: "material-symbols:notes",
    value: "text",
    helpingText: "Generate a column of text values.",
  },
  {
    label: "Boolean",
    icon: "material-symbols:toggle-on-outline",
    value: "boolean",
    helpingText: "Generate a column of boolean values.",
  },
  {
    label: "Integer",
    icon: "material-symbols:tag",
    value: "integer",
    helpingText: "Generate a column of number values.",
  },
  {
    label: "Float",
    icon: "tabler:decimal",
    value: "float",
    helpingText: "Generate a column of float values.",
  },
  {
    label: "JSON",
    icon: "material-symbols:data-object",
    value: "json",
    helpingText: "Generate a column of json values.",
  },
  {
    label: "Array",
    icon: "material-symbols:data-array",
    value: "array",
    helpingText: "Generate a column of array values.",
  },
  {
    label: "Date Time",
    icon: "tabler:calendar",
    value: "datetime",
    helpingText: "Generate a column of date time values.",
  },
  {
    label: "Audio",
    icon: "fluent:speaker-2-16-regular",
    value: "audio",
    helpingText: "Generate a column of audio type.",
  },
  {
    label: "Image",
    icon: "material-symbols:image-outline",
    value: "image",
    helpingText: "Generate a column of image values.",
  },
  {
    label: "Images",
    icon: "material-symbols:art-track-outline",
    value: "images",
    helpingText: "Generate a column for multiple images.",
  },
  {
    label: "Document",
    icon: "fluent:document-square-28-regular",
    value: "document",
    helpingText: "Generate a column of document values.",
  },
];

// Owner the capability contract-10-22: Execute Custom Code stays out of the Add column
// menu (and the Conditional Node branches) until its backend test is green.
export const EXECUTE_CODE_COLUMN_OFFERED = false;

/** How a Save re-runs an edited dynamic column. */
export const RERUN_SCOPES = [
  { value: "all", label: "Re-run all rows" },
  { value: "changed", label: "Only rows whose inputs changed" },
  { value: "failed", label: "Only failed cells" },
];

export const DynamicColumns = [
  {
    label: "Run Prompt",
    icon: "token:rune",
    value: "run_prompt",
    color: "text.primary",
    helpingText:
      "Generate a column and new entries for every row by making inference calls to LLM using a specific prompt template.",
  },
  {
    label: "Retrieval",
    icon: "solar:widget-broken",
    value: "retrieval",
    color: "text.primary",
    helpingText:
      "Generate a column and new entries for every row by retrieving from chunk database.",
  },
  {
    label: "Extract Entities",
    icon: "material-symbols:chip-extraction",
    value: "extractEntities",
    color: "text.primary",
    helpingText:
      "Generate a new column by extracting entities from the text using a specified model.",
  },
  {
    label: "Extract a JSON Key",
    icon: "mdi:code-json",
    value: "extractJsonKey",
    color: "text.primary",
    // IT-R2 G5 P5: opens the path picker (sample values, rows with a value).
    helpingText:
      "Add a nested value as its own column, e.g. metadata.customer.tier: pick the path, see sample values and how many rows have it.",
    keywords: [
      "path",
      "nested",
      "extract",
      "json",
      "key",
      "field",
      "column from path",
      "jsonpath",
    ],
  },
  // Execute Custom Code (L5 G4): built (sandboxed Python, network off,
  // 5 s / 256 MB limits) and offered only once its backend test is green.
  ...(EXECUTE_CODE_COLUMN_OFFERED
    ? [
        {
          icon: "mdi:code",
          label: "Execute Custom Code",
          value: "executeCustomCode",
          color: "text.primary",
          helpingText:
            "Run a Python main(row) on every row in a sandbox: network off, 5 s and 256 MB per cell.",
        },
      ]
    : []),
  {
    label: "Classification",
    icon: "mingcute:classify-2-line",
    value: "classification",
    color: "text.primary",
    helpingText:
      "Generate a new column by classifying the text using a specified model.",
  },
  {
    label: "API Calls",
    value: "apiCall",
    color: "text.primary",
    icon: "hugeicons:api",
    helpingText:
      "Generate a column and new entries for every row by making API calls to a specified endpoint.",
  },
  {
    label: "Conditional Node",
    icon: "hugeicons:node-add",
    color: "text.primary",
    value: "conditionalnode",
    helpingText:
      "Apply different actions to your data based on specified conditions, allowing for branching logic.",
  },
  // HO D7 (the interface contract): Expression, where add_expression_column is served.
  ...(expressionColumnOffered() ? [EXPRESSION_COLUMN_OPTION] : []),
];

export const replaceColumnIdWithName = (text, allColumns) => {
  let updatedText = text;
  allColumns.forEach(({ headerName, field }) => {
    const pattern = new RegExp(
      `{{\\s*${field}((?:\\.[^}\\s]+|\\[\\d+\\])*)\\s*}}`,
      "g",
    );
    updatedText = updatedText.replace(pattern, `{{${headerName}$1}}`);
  });

  return updatedText;
};

export const replaceColumnNameWithId = (text, allColumns) => {
  let newText = text;
  allColumns.forEach(({ headerName, field }) => {
    // Note: column names containing dots are not supported (dot is treated as a path separator)
    const pattern = new RegExp(
      `{{${headerName}((?:\\.[^}\\s]+|\\[\\d+\\])*)}}`,
      "g",
    );
    if (newText && newText.length) {
      newText = newText.replace(pattern, `{{${field}$1}}`);
    }
  });
  return newText;
};

export const transformDynamicColumnConfig = (type, config, allColumns) => {
  switch (type) {
    case "api_call":
      return {
        config: {
          ...config,
          url: replaceColumnIdWithName(config?.url || "", allColumns),
          body: replaceColumnIdWithName(
            typeof config?.body === "string"
              ? config.body
              : JSON.stringify(config?.body) || "",
            allColumns,
          ),
          params: Object.entries(config?.params || {}).map(([key, value]) => ({
            id: getRandomId(),
            name: key,
            value:
              value.type === "Variable"
                ? replaceColumnIdWithName(value.value, allColumns)
                : value.value,
            type: value.type,
          })),
          headers: Object.entries(config?.headers || {}).map(
            ([key, value]) => ({
              id: getRandomId(),
              name: key,
              value:
                value.type === "Variable"
                  ? replaceColumnIdWithName(value.value, allColumns)
                  : value.value,
              type: value.type,
            }),
          ),
        },
        concurrency: config?.concurrency,
      };
    case "classification": {
      const { labels, ...rest } = config;
      return {
        ...rest,
        labels: labels?.map((item) => ({
          id: getRandomId(),
          value: item,
        })),
      };
    }
    default:
      return config;
  }
};

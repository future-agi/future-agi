import { getRandomId } from "src/utils/utils";

export const StaticColumns = [
  {
    label: "Text",
    icon: "solar:notes-linear",
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
    icon: "solar:hashtag-linear",
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
    icon: "solar:braces-linear",
    value: "json",
    helpingText: "Generate a column of json values.",
  },
  {
    label: "Array",
    icon: "solar:brackets-linear",
    value: "array",
    helpingText: "Generate a column of array values.",
  },
  {
    label: "Date Time",
    icon: "solar:calendar-linear",
    value: "datetime",
    helpingText: "Generate a column of date time values.",
  },
  {
    label: "Audio",
    icon: "solar:volume-loud-linear",
    value: "audio",
    helpingText: "Generate a column of audio type.",
  },
  {
    label: "Image",
    icon: "solar:gallery-linear",
    value: "image",
    helpingText: "Generate a column of image values.",
  },
  {
    label: "Images",
    icon: "solar:gallery-linear",
    value: "images",
    helpingText: "Generate a column for multiple images.",
  },
  {
    label: "Document",
    icon: "solar:document-text-linear",
    value: "document",
    helpingText: "Generate a column of document values.",
  },
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
    icon: "solar:cpu-linear",
    value: "extractEntities",
    color: "text.primary",
    helpingText:
      "Generate a new column by extracting entities from the text using a specified model.",
  },
  {
    label: "Extract a JSON Key",
    icon: "solar:code-linear",
    value: "extractJsonKey",
    color: "text.primary",
    helpingText:
      "Extract specific keys from JSON using JSONPath syntax, allowing for target retreival of nested information within JSON objects.",
  },
  // {
  //   icon: "solar:code-linear",
  //   label: "Execute Custom Code",
  //   value: "executeCustomCode",
  //   color: "text.primary",
  //   helpingText:
  //     "Execute custom Python code to perform data transformations or complex operations.",
  // },
  {
    label: "Classification",
    icon: "solar:tag-horizontal-linear",
    value: "classification",
    color: "text.primary",
    helpingText:
      "Generate a new column by classifying the text using a specified model.",
  },
  {
    label: "API Calls",
    value: "apiCall",
    color: "text.primary",
    icon: "solar:plug-circle-linear",
    helpingText:
      "Generate a column and new entries for every row by making API calls to a specified endpoint.",
  },
  {
    label: "Conditional Node",
    icon: "solar:widget-add-linear",
    color: "text.primary",
    value: "conditionalnode",
    helpingText:
      "Apply different actions to your data based on specified conditions, allowing for branching logic.",
  },
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

export const id = "11111111-1111-4111-8111-111111111111";
export const scope = { organizationId: id, workspaceId: id, projectId: id };
// Test-only fixtures of B05's actual typed-capabilities response.
export const capabilities = {
  sources: [
    {
      value_type: "number",
      reducers: ["count", "first", "latest", "max", "mean", "min", "sum"],
      operators: ["eq", "ne", "lt", "lte", "gt", "gte"],
      observation_reducers: ["any", "all"],
      ordering: "sentence",
    },
    {
      value_type: "boolean",
      reducers: ["all", "any", "count", "first", "latest"],
      operators: ["eq", "ne"],
      observation_reducers: ["any", "all"],
      ordering: "sentence",
    },
    {
      value_type: "text",
      reducers: ["count", "first", "latest"],
      operators: ["eq", "ne"],
      observation_reducers: ["any", "all"],
      ordering: "sentence",
    },
  ],
  kinds: [
    "share",
    "average",
    "total",
    "count",
    "unique_count",
    "percentile",
    "custom",
  ],
  levels: ["conversation", "span", "user"],
  user_rules: {
    share: ["any", "every"],
    count: ["any", "every"],
    average: ["average", "total"],
  },
  max_group_dims: 2,
  preview_max_days: 7,
  preview_budget_seconds: 15,
};
export const catalog = {
  status: "success",
  result: {
    metrics: [
      {
        property_id: "custom_attribute:locale",
        property_kind: "custom_attribute",
        name: "locale",
        display_name: "Locale",
        type: "string",
        attribute_types: ["string"],
      },
      {
        property_id: "custom_attribute:resolved",
        property_kind: "custom_attribute",
        name: "resolved",
        display_name: "Resolved",
        type: "boolean",
        attribute_types: ["boolean"],
      },
      {
        property_id: "custom_attribute:cost",
        property_kind: "custom_attribute",
        name: "price",
        display_name: "Price",
        type: "number",
        attribute_types: ["number"],
      },
    ],
    next_cursor: null,
  },
};
export const evaluators = {
  results: [{ id, config_hash: "a".repeat(64), name: "Quality" }],
  truncated: false,
};

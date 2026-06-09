import { AnnotationLabelTypes, TraceSpanColType } from "./constants";

export const getEvaluationMetricFilterDefinition = (columns) => {
  const group = "Evaluation Metrics";
  const filteredColumns = columns.filter((col) => col.groupBy === group);

  // Only return filter definition if there are evaluation metrics
  if (filteredColumns.length === 0) {
    return [];
  }

  const tempDef = [];

  const obj = {
    propertyName: group,
    stringConnector: "is",
    dependents: filteredColumns.map((col) => {
      // For Pass/Fail output type, provide a dropdown with Passed/Failed options
      if (col.outputType === "Pass/Fail") {
        const isReversed = col.reverseOutput === true;
        // For normal evals: Passed = 0, Failed = 100
        // For reversed evals: Passed = 100, Failed = 0
        const passedValue = isReversed ? 100 : 0;
        const failedValue = isReversed ? 0 : 100;

        return {
          propertyName: col.name,
          propertyId: col.id,
          maxUsage: 1,
          filterType: {
            type: "number",
            options: [
              { label: "Passed", value: passedValue },
              { label: "Failed", value: failedValue },
            ],
          },
        };
      }

      // Default: number filter for other eval types
      return {
        propertyName: col.name,
        propertyId: col.id,
        maxUsage: 1,
        filterType: {
          type: "number",
        },
      };
    }),
  };
  tempDef.push(obj);

  return tempDef;
};

export const getAnnotationDefinition = (col, _source = null) => {
  switch (col.annotationLabelType) {
    case AnnotationLabelTypes.STAR:
      return {
        propertyName: col.name,
        propertyId: col.id,
        maxUsage: 1,
        filterType: {
          type: "number",
          options: Array.from(
            { length: col?.settings?.noOfStars || 5 },
            (_, i) => i + 1,
          ).map((i) => ({
            label: `${i} Star${i > 1 ? "s" : ""}`,
            value: i,
          })),
        },
      };
    case AnnotationLabelTypes.CATEGORICAL:
      // if (source === PROJECT_SOURCE.SIMULATOR) {
      return {
        propertyName: col.name,
        propertyId: col.name,
        maxUsage: 1,
        stringConnector: "is",
        filterType: { type: "text" },
        dependents: (col?.settings?.options || []).map(({ label }) => ({
          propertyName: label,
          propertyId: `${col.id}**${label}`,
          maxUsage: 1,
          filterType: { type: "number" },
          defaultFilter: "greater_than",
          defaultFilterValue: 0,
          hideValueSelector: true,
        })),
      };

    case AnnotationLabelTypes.THUMBS_UP_DOWN:
      return {
        propertyName: col.name,
        propertyId: col.name,
        maxUsage: 1,
        stringConnector: "is",
        filterType: { type: "text" },
        dependents: [
          {
            propertyName: "Thumbs up",
            propertyId: `${col.id}**thumbs_up`,
            maxUsage: 1,
            filterType: { type: "number" },
            defaultFilter: "greater_than",
            defaultFilterValue: 0,
            hideValueSelector: true,
          },
          {
            propertyName: "Thumbs down",
            propertyId: `${col.id}**thumbs_down`,
            maxUsage: 1,
            filterType: { type: "number" },
            defaultFilter: "greater_than",
            defaultFilterValue: 0,
            hideValueSelector: true,
          },
        ],
      };
    case AnnotationLabelTypes.TEXT:
      return {
        propertyName: col.name,
        propertyId: col.id,
        maxUsage: 1,
        showOperator: true,
        filterType: { type: "text" },
      };
    case AnnotationLabelTypes.NUMERIC:
      return {
        propertyName: col.name,
        propertyId: col.id,
        maxUsage: 1,
        filterType: { type: "number" },
      };
  }
  return {};
};
export const getSystemMetricFilterDefinition = () => {
  const group = "System Metrics";
  const obj = {
    propertyName: group,
    stringConnector: "is",
    dependents: [
      {
        propertyName: "Agent latency",
        propertyId: "avg_agent_latency_ms",
        stringConnector: "is",
        filterType: { type: "number" },
      },
      {
        propertyName: "Turn count",
        propertyId: "turn_count",
        stringConnector: "is",
        filterType: { type: "number" },
      },
      {
        propertyName: "% agent talk",
        propertyId: "agent_talk_percentage",
        stringConnector: "is",
        filterType: { type: "number" },
      },
      {
        propertyName: "Agent WPM",
        propertyId: "bot_wpm",
        stringConnector: "is",
        filterType: { type: "number" },
      },
      {
        propertyName: "User interruptions",
        propertyId: "user_interruption_count",
        stringConnector: "is",
        filterType: { type: "number" },
      },
      {
        propertyName: "Agent interruption",
        propertyId: "ai_interruption_count",
        stringConnector: "is",
        filterType: { type: "number" },
      },
      {
        propertyName: "Call type",
        propertyId: "call_type",
        stringConnector: "is",
        filterType: {
          type: "option",
          options: [
            { label: "Inbound", value: "inbound" },
            { label: "Outbound", value: "outbound" },
          ],
        },
      },
      {
        propertyName: "Ended reason",
        propertyId: "ended_reason",
        stringConnector: "is",
        filterType: { type: "text" },
      },
    ],
  };
  return [obj];
};
export const getAnnotationMetricFilterDefinition = (columns) => {
  const group = "Annotation Metrics";
  const filteredColumns = (columns || []).filter((col) => col.groupBy === group);

  if (filteredColumns.length === 0) {
    return [];
  }

  const annotatorsMap = {};
  filteredColumns.forEach((col) => {
    Object.values(col?.annotators || {}).forEach((annotator) => {
      const uid = annotator?.user_id ?? annotator?.userId;
      if (uid && !annotatorsMap[uid]) {
        annotatorsMap[uid] = { ...annotator };
      }
    });
  });
  const allAnnotators = Object.values(annotatorsMap);

  const obj = {
    propertyName: group,
    stringConnector: "by",
    dependents: [
      {
        propertyName: "Label name",
        propertyId: "label_name",
        stringConnector: "is",
        filterType: { type: "text" },
        dependents: filteredColumns.map((col) => getAnnotationDefinition(col)),
      },
      {
        propertyName: "Annotator",
        propertyId: "annotator",
        maxUsage: 1,
        multiSelect: true,
        filterType: {
          type: "option",
          options: allAnnotators.map((annotator) => ({
            label: annotator.user_name ?? annotator.userName,
            value: annotator.user_id ?? annotator.userId,
          })),
        },
      },
      {
        propertyName: "My Annotations",
        propertyId: "my_annotations",
        maxUsage: 1,
        filterType: { type: "boolean" },
        defaultFilterValue: true,
        hideValueSelector: true,
      },
    ],
  };

  return [obj];
};

export const getFilterExtraProperties = (val) => {
  const colType = TraceSpanColType?.[val?._meta?.parentProperty];
  if (!colType) {
    return {};
  }
  return {
    colType,
  };
};

export const getAttributesDefinition = (
  attributes = [],
  existingFilter = [],
) => {
  const group = "Attribute";
  const attrFilters = Array.isArray(existingFilter)
    ? existingFilter.filter((f) => f?._meta?.parentProperty === group)
    : [];

  const attrFilterTypeHash = attrFilters.reduce((acc, filter) => {
    if (filter?.filterConfig?.filterType) {
      acc[filter?.columnId] = filter.filterConfig?.filterType;
    }
    return acc;
  }, {});

  const tempDef = [];

  const obj = {
    propertyName: group,
    stringConnector: "is",
    dependents: attributes?.map((attr) => {
      // Support both enriched { key, type, count } and plain string formats
      const isEnriched = typeof attr === "object" && attr !== null;
      const attrKey = isEnriched ? attr.key : attr;
      const attrType = isEnriched ? attr.type : null;

      // Use existing filter type if set, otherwise infer from enriched metadata
      const existingType = attrFilterTypeHash?.[attrKey];
      let type;
      if (existingType) {
        type = existingType;
      } else if (attrType === "number") {
        type = "number";
      } else if (attrType === "boolean") {
        type = "boolean";
      } else {
        type = "text";
      }

      const filterType = {
        type,
        ...(type === "boolean" && {
          truthLabel: "True",
          falseLabel: "False",
        }),
      };
      return {
        propertyName: attrKey,
        propertyId: attrKey,
        maxUsage: 1,
        allowTypeChange: true,
        showOperator: true,
        filterType,
        // Flag for autocomplete on string attributes
        ...(type === "text" && isEnriched && { asyncOptions: true }),
      };
    }),
  };

  tempDef.push(obj);
  return tempDef;
};

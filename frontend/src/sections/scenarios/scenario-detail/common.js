import isEqual from "lodash/isEqual";
import { z } from "zod";

// helper to normalize graph (remove transient fields)
// ⚠️ NOTE: nodes/edges are transformed in transformNode/transformEdge
// if you change what fields they put in, you gotta update normalizeGraph too
// src/components/GraphBuilder/common.js
export function normalizeGraph(graph) {
  const normalizeNodes = graph.newNodes.map(({ measured, data, ...rest }) => ({
    ...rest,
    position: undefined,
    selected: undefined,
    dragging: undefined,
    deletable: undefined,
    data: {
      ...data,
      editMode: undefined, // ignore editMode
      highlightColor: undefined, // ignore transient orphan highlight
    },
  }));

  const normalizeEdges = graph.newEdges.map(({ id, ...rest }) => ({
    ...rest,
    id: undefined, // ignore edge id
  }));

  return {
    newNodes: normalizeNodes,
    newEdges: normalizeEdges,
  };
}

export function hasGraphChanged(initial, current) {
  const normalizedInitial = normalizeGraph(initial);
  const normalizedCurrent = normalizeGraph(current);

  return !isEqual(normalizedInitial, normalizedCurrent);
}

export const columnGenerationOptions = [
  {
    label: "Add Manually",
    value: "manual",
  },
  { label: "Generate using AI", value: "ai-generate" },
];

export const addColumnSchema = z.object({
  mode: z.enum(["manual", "ai-generate"], {
    message: "Mode must be either 'manual' or 'ai-generate'",
  }),

  columns: z
    .array(
      z.object({
        name: z
          .string()
          .min(1, "Column name is required")
          .max(50, "Column name must be less than 50 characters"),

        type: z.enum(
          ["text", "boolean", "integer", "float", "json", "array", "datetime"],
          { message: "Data type is required" },
        ),

        description: z
          .string()
          .min(1, "Description is required")
          .max(200, "Description must be less than 200 characters"),
      }),
    )
    .min(1, "At least one column is required"),
});

export const dataTypeOptions = [
  { label: "Text", value: "text" },
  { label: "Boolean", value: "boolean" },
  { label: "Integer", value: "integer" },
  { label: "Float", value: "float" },
  { label: "JSON", value: "json" },
  { label: "Array", value: "array" },
  { label: "Date & Time", value: "datetime" },
];

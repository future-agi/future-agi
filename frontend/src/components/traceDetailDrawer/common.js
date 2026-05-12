import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const chipBackgroundColor = {
  Passed: "green.o10",
  Failed: "red.o10",
};

const chipLabelColor = {
  Passed: "green.500",
  Failed: "red.500",
};

const keywordMapping = {
  true: "Passed",
  false: "Failed",
};

export const parseArrayString = (value) => {
  try {
    return JSON.parse(value.replace(/'/g, '"'));
  } catch {
    return [value];
  }
};

export const getChipLabel = (data) => {
  const cellValue = data?.score;

  if (cellValue == null || cellValue === "error") return "error";

  if (typeof cellValue === "boolean") {
    return keywordMapping[cellValue];
  }

  if (typeof cellValue === "number") {
    return `${(cellValue * 100).toFixed(0)}%`;
  }
  return cellValue;
};

const getColorBasedOnScore = (numericValue) => {
  let color, backgroundColor;
  if (numericValue <= 49) {
    color = "red.500";
    backgroundColor = "red.o10";
  } else if (numericValue <= 79) {
    color = "orange.500";
    backgroundColor = "orange.o10";
  } else if (numericValue <= 100) {
    color = "green.500";
    backgroundColor = "green.o10";
  } else {
    color = "green.500";
    backgroundColor = "green.o10";
  }
  return {
    color,
    backgroundColor,
  };
};

export const getChipColor = (data) => {
  const score = data?.score;
  const cellValue = keywordMapping[score];
  if (typeof score === "boolean") {
    return chipBackgroundColor[cellValue].toString();
  } else if (typeof score === "number") {
    return getColorBasedOnScore(data?.score * 100).backgroundColor;
  } else if (Array.isArray(score)) {
    return "action.hover";
  }
  return "red.o10";
};

export const getFontColor = (data) => {
  const score = data?.score;
  const cellValue = keywordMapping[score];
  if (typeof score === "boolean") {
    return chipLabelColor[cellValue].toString();
  } else if (typeof score === "number") {
    return getColorBasedOnScore(data?.score * 100).color;
  } else if (Array.isArray(score)) {
    return "primary.main";
  }
  return "red.500";
};

// Fields pre-checked by default when adding to dataset
export const coreSpanFields = [
  "id",
  "input",
  "output",
  "model",
  "observation_type",
  "latency_ms",
  "total_tokens",
  "completion_tokens",
  "prompt_tokens",
];

// All fields available for mapping (core + extended)
export const defaultSpanFields = [
  ...coreSpanFields,
  "name",
  "operation_name",
  "status",
  "status_message",
  "provider",
  "cost",
  "model_parameters",
  "metadata",
  "span_attributes",
  "resource_attributes",
  "tags",
  "span_events",
  "start_time",
  "end_time",
  "child_spans",
  "eval_metrics",
  "annotation_metrics",
];

export function extractStringValues(data) {
  return data?.values?.map((v) => v?.stringValue)?.filter(Boolean) || [];
}

export const useTraceErrorAnalysis = (id) => {
  return useQuery({
    queryKey: ["trace-error-analysis", id],
    queryFn: () => axios.get(endpoints.project.getTraceErrorAnalysis(id)),
    select: (res) => res?.data?.result,
    enabled: !!id,
  });
};

export const getObservationSpanById = (treeData, id) => {
  for (const node of treeData) {
    if (node.observation_span.id === id) return node.observation_span;
    if (node.children) {
      const found = getObservationSpanById(node.children, id);
      if (found) return found;
    }
  }
};

export const TRACE_ICON_MAP = {
  agent: "/icons/tracedetails/agent_icon.svg",
  chain: "/icons/tracedetails/chain_icon.svg",
  embedding: "/icons/tracedetails/embedding_icon.svg",
  evaluator: "/icons/tracedetails/evaluator_icon.svg",
  guardrail: "/icons/tracedetails/guardrail_icon.svg",
  llm: "/icons/tracedetails/llm_icon.svg",
  reranker: "/icons/tracedetails/reranker_icon.svg",
  retriever: "/icons/tracedetails/retriever_icon.svg",
  tool: "/icons/tracedetails/tool_icon.svg",
  conversation: "/icons/tracedetails/conversation_icon.svg",
  unknown: "/icons/tracedetails/unknown_icon.svg",
};

export const getTraceIconLabel = (key) => {
  return TRACE_ICON_MAP[key] ?? TRACE_ICON_MAP.unknown;
};

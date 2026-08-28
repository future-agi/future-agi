import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import {
  NODE_TYPES,
  NODE_TYPE_CONFIG,
} from "../../sections/agent-playground/utils/constants";

/**
 * Hook for fetching graphs that can be referenced as agent nodes.
 * @param {string} graphId - The current graph's ID
 * @param {object} options - Additional react-query options
 */
export const useGetReferenceableGraphs = (graphId, options = {}) =>
  useQuery({
    queryKey: ["agent-playground", "referenceable-graphs", graphId],
    queryFn: () =>
      axios.get(endpoints.agentPlayground.referenceableGraphs(graphId)),
    select: (res) => res.data?.result?.graphs ?? [],
    staleTime: 30 * 1000,
    enabled: !!graphId,
    ...options,
  });

/**
 * Hook for fetching node templates.
 * Maps API shape to NodeCard shape: { id, node_template_id, title, description, iconSrc, color }
 * @param {object} options - Additional react-query options
 */
const PALETTE_TEMPLATE_NAMES = [
  NODE_TYPES.LLM_PROMPT,
  NODE_TYPES.HTTP_REQUEST,
];

export const useGetNodeTemplates = (options = {}) =>
  useQuery({
    queryKey: ["agent-playground", "node-templates"],
    queryFn: () => axios.get(endpoints.agentPlayground.nodeTemplates),
    select: (res) =>
      (res.data?.result?.node_templates ?? [])
        .filter((t) => PALETTE_TEMPLATE_NAMES.includes(t.name))
        .map((t) => {
          const typeConfig = NODE_TYPE_CONFIG[t.name] || {
            iconSrc: "/assets/icons/ic_chat_single.svg",
            color: "orange.500",
          };
          return {
            id: t.name,
            node_template_id: t.id,
            title: t.display_name,
            description: t.description,
            iconSrc: typeConfig.iconSrc,
            color: typeConfig.color,
          };
        }),
    staleTime: 5 * 60 * 1000,
    ...options,
  });

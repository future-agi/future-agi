import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { NODE_TYPES } from "../../sections/agent-playground/utils/constants";

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
 * Hook for fetching node templates. Filters to llm_prompt only for now.
 * Maps API shape to NodeCard shape: { id, node_template_id, title, description, iconSrc, color }
 * @param {object} options - Additional react-query options
 */
export const useGetNodeTemplates = (options = {}) =>
  useQuery({
    queryKey: ["agent-playground", "node-templates"],
    queryFn: () => axios.get(endpoints.agentPlayground.nodeTemplates),
    select: (res) =>
      (res.data?.result?.node_templates ?? [])
        .filter((t) => t.name === NODE_TYPES.LLM_PROMPT)
        .map((t) => ({
          id: t.name,
          node_template_id: t.id,
          title: t.display_name,
          description: t.description,
          iconSrc: "/assets/icons/ic_chat_single.svg",
          color: "text.secondary",
        })),
    staleTime: 5 * 60 * 1000,
    ...options,
  });

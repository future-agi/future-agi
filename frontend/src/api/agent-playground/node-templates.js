import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { NODE_TYPE_CONFIG, NODE_TYPES } from "../../sections/agent-playground/utils/constants";

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

/** Node types whose templates are served by the backend API. */
const BACKEND_TEMPLATE_TYPES = new Set([NODE_TYPES.LLM_PROMPT]);

/**
 * Client-side node definitions for node types that are handled
 * entirely on the frontend and don't have a backend template entry.
 */
const CLIENT_SIDE_NODES = [
  {
    id: NODE_TYPES.HTTP_REQUEST,
    node_template_id: null,
    title: NODE_TYPE_CONFIG[NODE_TYPES.HTTP_REQUEST].title,
    description: NODE_TYPE_CONFIG[NODE_TYPES.HTTP_REQUEST].description,
    iconSrc: NODE_TYPE_CONFIG[NODE_TYPES.HTTP_REQUEST].iconSrc,
    color: NODE_TYPE_CONFIG[NODE_TYPES.HTTP_REQUEST].color,
  },

];

/**
 * Hook for fetching node templates.
 * Backend templates are merged with client-side node definitions so that
 * HTTP Request and Conditional nodes appear in the node selection panel.
 * Maps API shape to NodeCard shape: { id, node_template_id, title, description, iconSrc, color }
 * @param {object} options - Additional react-query options
 */
export const useGetNodeTemplates = (options = {}) =>
  useQuery({
    queryKey: ["agent-playground", "node-templates"],
    queryFn: () => axios.get(endpoints.agentPlayground.nodeTemplates),
    select: (res) => {
      const backendNodes = (res.data?.result?.node_templates ?? [])
        .filter((t) => BACKEND_TEMPLATE_TYPES.has(t.name))
        .map((t) => ({
          id: t.name,
          node_template_id: t.id,
          title: t.display_name,
          description: t.description,
          iconSrc: NODE_TYPE_CONFIG[t.name]?.iconSrc ?? "/assets/icons/ic_chat_single.svg",
          color: NODE_TYPE_CONFIG[t.name]?.color ?? "orange.500",
        }));
      return [...backendNodes, ...CLIENT_SIDE_NODES];
    },
    staleTime: 5 * 60 * 1000,
    ...options,
  });


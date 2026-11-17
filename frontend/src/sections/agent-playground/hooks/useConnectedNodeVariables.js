import { useMemo, useEffect } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { useNodeConnections } from "@xyflow/react";
import { useGetGraphDataset } from "src/api/agent-playground/agent-playground";
import { useGetPossibleEdgeMappings } from "src/api/agent-playground/nodes";
import { useAgentPlaygroundStoreShallow } from "../store";
import { NODE_TYPES } from "../utils/constants";
import { isUUID } from "src/utils/utils";

/**
 * Derive a flat { [colName]: value } map from the dataset API response.
 */
function deriveVariablesFromDataset(dataset) {
  if (!dataset?.columns?.length) return {};
  const row = dataset.rows?.[0] || null;
  const variables = {};
  for (const col of dataset.columns) {
    const cell = row?.cells?.find(
      (c) => (c.columnId || c.column_id) === col.id,
    );
    const val = cell?.value;
    if (val != null && val !== "") {
      variables[col.name] = val;
    }
  }
  return variables;
}

function getMappingOutputPorts(mapping) {
  return mapping?.outputPorts || mapping?.output_ports || [];
}

function getMappingSourceNodeName(mapping) {
  return mapping?.sourceNodeName || mapping?.source_node_name || "";
}

function getPortDisplayName(port) {
  return port?.displayName || port?.display_name;
}

function getPortDataSchema(port) {
  return port?.dataSchema || port?.data_schema;
}

// A plain variable name: no dots, brackets or Jinja expression syntax.
const PLAIN_VARIABLE_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

/**
 * Derives connected input nodes, dropdown options, and a variable validator
 * for the prompt editor mention system.
 *
 * Fetches the graph dataset (global variables) on mount and invalidates
 * the query so variables are always fresh when the form opens.
 *
 * @param {string} nodeId — the node currently being edited
 * @returns {{ connectedNodes, dropdownOptions, validateVariable }}
 */
export default function useConnectedNodeVariables(
  nodeId,
  { includeDatasetVars = true } = {},
) {
  const { agentId } = useParams();
  const [searchParams] = useSearchParams();
  const graphId = agentId;
  const versionId = searchParams.get("version");
  const queryClient = useQueryClient();

  const { nodes } = useAgentPlaygroundStoreShallow((s) => ({
    nodes: s.nodes,
  }));

  // Subscribe to incoming connections for connectedNodes derivation
  const incomingConnections = useNodeConnections({
    id: nodeId,
    handleType: "target",
  });

  // Derive a stable key from incoming edges so the edge-mappings query
  // automatically refetches when connections change — no manual
  // invalidateQueries needed from every caller.
  const incomingEdgeKey = useMemo(
    () =>
      incomingConnections
        .map((c) => c.source)
        .sort()
        .join(","),
    [incomingConnections],
  );

  const { data: edgeMappingsData, isLoading: isLoadingEdgeMappings } =
    useGetPossibleEdgeMappings(graphId, versionId, nodeId, {
      staleTime: 0,
      refetchOnMount: "always",
      placeholderData: keepPreviousData,
      queryKey: [
        "agent-playground",
        "possible-edge-mappings",
        graphId,
        versionId,
        nodeId,
        incomingEdgeKey,
      ],
    });

  // Invalidate the dataset query on mount so we always get fresh variables
  useEffect(() => {
    if (graphId) {
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graph-dataset", graphId, versionId],
      });
    }
  }, [graphId, versionId, queryClient]);

  // Fetch the dataset (global variables)
  const { data: datasetData, isLoading: isLoadingDataset } = useGetGraphDataset(
    graphId,
    versionId,
    {
      enabled: !!graphId,
    },
  );

  // Derive global variables directly from API response
  const globalVariables = useMemo(
    () => deriveVariablesFromDataset(datasetData),
    [datasetData],
  );

  const connectedNodes = useMemo(() => {
    if (!incomingConnections.length) return [];
    return incomingConnections
      .map((conn) => {
        const storeNode = nodes.find((n) => n.id === conn.source);
        if (!storeNode) return null;
        const outputPort = storeNode.data?.ports?.find(
          (p) => p.direction === "output",
        );
        return {
          id: storeNode.id,
          type: storeNode.type || NODE_TYPES.LLM_PROMPT,
          label: storeNode.data?.label,
          outputLabel: outputPort?.display_name,
          responseFormat:
            storeNode.data?.config?.modelConfig?.responseFormat || "text",
        };
      })
      .filter(Boolean);
  }, [incomingConnections, nodes]);

  const dropdownOptions = useMemo(() => {
    const options = (edgeMappingsData || []).flatMap((mapping) =>
      getMappingOutputPorts(mapping)
        .filter((port) => port.direction === "output")
        .map((port) => ({
          id: `${getMappingSourceNodeName(mapping)}.${getPortDisplayName(port)}`,
          value: `${getMappingSourceNodeName(mapping)}.${getPortDisplayName(port)}`,
        })),
    );

    if (includeDatasetVars) {
      const globalKeys = (datasetData?.columns || []).map((col) => col.name);
      globalKeys.forEach((key) => {
        options.push({ id: key, value: key });
      });
    }

    // Dedupe — keepPreviousData can cause overlap between old and new results
    const seen = new Set();
    return options.filter((o) => {
      if (seen.has(o.value)) return false;
      seen.add(o.value);
      return true;
    });
  }, [edgeMappingsData, datasetData, includeDatasetVars]);

  /**
   * Determines if a variable name is valid (green) or invalid (red).
   *
   * Rules:
   * 1. "node_label.output_label" — valid if node is connected
   * 2. "node_label.output_label.key..." — valid if connected node has JSON/schema response
   * 3. "custom_var" — valid if it exists in global variables (dataset). For a
   *    plain name, spaces inside the braces ({{ custom_var }}) are ignored, as
   *    the backend ignores them.
   * 4. A plain name with no value yet that is neither a connected node's
   *    output nor a connected node's label — null (left plain, not red).
   *    Saving makes it an input of this agent and Run asks for its value; the
   *    form says so in words (UnsetVariablesNote).
   */
  const validateVariable = useMemo(() => {
    const exactNodeVars = new Set(
      connectedNodes.map((n) => `${n.label}.${n.outputLabel}`),
    );
    // Only edge mapping vars (connected nodes) are valid — global vars
    // are validated separately via globalKeys (which checks assigned values)
    const edgeMappingVars = new Set(
      (edgeMappingsData || []).flatMap((mapping) =>
        getMappingOutputPorts(mapping)
          .filter((port) => port.direction === "output")
          .map(
            (port) =>
              `${getMappingSourceNodeName(mapping)}.${getPortDisplayName(port)}`,
          ),
      ),
    );

    // Build prefixes for ports with object dataSchema from edge mappings API
    const objectPrefixes = (edgeMappingsData || []).flatMap((mapping) =>
      getMappingOutputPorts(mapping)
        .filter(
          (port) =>
            port.direction === "output" &&
            getPortDataSchema(port)?.type === "object",
        )
        .map(
          (port) =>
            `${getMappingSourceNodeName(mapping)}.${getPortDisplayName(port)}.`,
        ),
    );

    const jsonPrefixes = connectedNodes
      .filter(
        (n) =>
          n.responseFormat === "json" ||
          typeof n.responseFormat === "object" ||
          isUUID(n.responseFormat),
      )
      .map((n) => `${n.label}.${n.outputLabel}.`);

    const allPrefixes = [...jsonPrefixes, ...objectPrefixes];
    const globalKeys = new Set(Object.keys(globalVariables));
    // Names a connected node owns. Saving wires a bare {{response}} to a
    // connected node's output of that name, so it is not an input of this
    // agent. A bare {{llm_prompt_1}} (a connected node's label) most likely
    // means one of that node's outputs, and "connect that node" would not
    // help. Neither belongs in the note, so both keep the result they had.
    const connectedNames = new Set(
      [
        ...connectedNodes.map((n) => n.outputLabel),
        ...connectedNodes.map((n) => n.label),
        ...(edgeMappingsData || []).flatMap((mapping) =>
          getMappingOutputPorts(mapping)
            .filter((port) => port.direction === "output")
            .map((port) => getPortDisplayName(port)),
        ),
        ...(edgeMappingsData || []).map((mapping) =>
          getMappingSourceNodeName(mapping),
        ),
      ].filter(Boolean),
    );

    return (variableName) => {
      if (exactNodeVars.has(variableName)) return true;
      if (edgeMappingVars.has(variableName)) return true;
      if (allPrefixes.some((prefix) => variableName.startsWith(prefix)))
        return true;
      if (globalKeys.has(variableName)) return true;
      const name = String(variableName ?? "").trim();
      // {{ question }} with a value: only reached when the braces hold spaces
      // around a plain name (the unspaced name returned true just above).
      // The backend trims, so the run uses that value. Valid, as {{question}}.
      if (PLAIN_VARIABLE_NAME.test(name) && globalKeys.has(name)) return true;
      // A plain name with no value yet is not an error: saving makes it an
      // input of this agent and Run asks for its value. Leave it plain.
      if (PLAIN_VARIABLE_NAME.test(name) && !connectedNames.has(name))
        return null;
      return false;
    };
  }, [connectedNodes, edgeMappingsData, globalVariables]);
  return {
    connectedNodes,
    dropdownOptions,
    validateVariable,
    isLoading: isLoadingEdgeMappings || isLoadingDataset,
  };
}

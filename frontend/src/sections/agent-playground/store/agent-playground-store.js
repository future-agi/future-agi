import { startTransition } from "react";
import { create } from "zustand";
import { devtools } from "zustand/middleware";
import { useShallow } from "zustand/react/shallow";
import { CURRENT_ENVIRONMENT } from "src/config-global";
import { addEdge, applyNodeChanges, applyEdgeChanges } from "@xyflow/react";
import {
  CODE_EXECUTION_DEFAULT_CONFIG,
  CODE_EXECUTION_PORTS,
  NODE_TYPES,
  NODE_X_OFFSET,
  PORT_KEYS,
  PORT_DIRECTION,
} from "../utils/constants";
import { parseVersionResponse } from "../utils/versionPayloadUtils";
import _ from "lodash";
import { useWorkflowRunStore } from "./workflow-run-store";

const getNodeLabel = (nodeId) => nodeId;

// Helper to generate unique node IDs
const generateNodeId = (type, nodes) => {
  const prefix = `${type}_node_`;
  const labelPrefix = `${type}_node_`;
  let maxNum = 0;
  for (const n of nodes) {
    // Check ID pattern (locally-created nodes)
    if (n.id.startsWith(prefix)) {
      const num = parseInt(n.id.slice(prefix.length), 10);
      if (num > maxNum) maxNum = num;
    }
    // Check label pattern (API-loaded nodes with UUID ids)
    const label = (n.data?.label || "").toLowerCase();
    if (label.startsWith(labelPrefix)) {
      const num = parseInt(label.slice(labelPrefix.length), 10);
      if (num > maxNum) maxNum = num;
    }
  }
  return `${prefix}${maxNum + 1}`;
};

// Helper to ensure a label is unique among existing nodes.
// If baseName already exists, appends _1, _2, etc.
const generateUniqueLabel = (baseName, nodes, suffix = "_") => {
  const existing = new Set(nodes.map((n) => n.data?.label || ""));
  if (!existing.has(baseName)) return baseName;
  let num = 1;
  while (existing.has(`${baseName}${suffix}${num}`)) num += 1;
  return `${baseName}${suffix}${num}`;
};

// Helper to get all output port display_names from a node.
// Non-agent nodes store ports at data.ports; agent nodes at data.config.payload.ports.
const getOutputPortLabels = (node) => {
  const directPorts = node?.data?.ports || [];
  const payloadPorts = node?.data?.config?.payload?.ports || [];
  return [...directPorts, ...payloadPorts]
    .filter((p) => p.direction === PORT_DIRECTION.OUTPUT)
    .map((p) => p.display_name);
};

// Helper to generate unique sequential output labels
export const generateOutputLabel = (nodes) => {
  const prefix = "response_";
  let maxNum = 0;
  for (const n of nodes) {
    for (const label of getOutputPortLabels(n)) {
      if (label?.startsWith(prefix)) {
        const num = parseInt(label.slice(prefix.length), 10);
        if (num > maxNum) maxNum = num;
      }
    }
  }
  return `${prefix}${maxNum + 1}`;
};

// Helper to calculate new node position
const calculateNodePosition = (nodes) => {
  if (nodes.length === 0) {
    return { x: 100, y: 0 };
  }

  const lastNode = nodes[nodes.length - 1];

  return {
    x: lastNode.position.x + NODE_X_OFFSET,
    y: lastNode.position.y,
  };
};

export const useAgentPlaygroundStore = create(
  devtools(
    (set, get, store) => ({
      // The current agent that is being viewed
      currentAgent: null,
      setCurrentAgent: (agent) =>
        set({ currentAgent: agent }, false, "setCurrentAgent"),

      updateVersion: (versionId, versionNumber, options = {}) => {
        const currentAgent = get().currentAgent;
        if (!currentAgent) return;
        set(
          {
            currentAgent: {
              ...currentAgent,
              version_id: versionId,
              version_name: `Version ${versionNumber}`,
              ...(options.is_draft !== undefined && {
                is_draft: options.is_draft,
              }),
              ...(options.version_status !== undefined && {
                version_status: options.version_status,
              }),
            },
          },
          false,
          "updateVersion",
        );

        // Update URL params
        const url = new URL(window.location);
        if (versionId) {
          url.searchParams.set("version", versionId);
        } else {
          url.searchParams.delete("version");
        }
        window.history.replaceState({}, "", url);
      },

      // Whether the version graph has been loaded from the API
      isGraphReady: false,

      // React Flow nodes and edges
      nodes: [],
      edges: [],

      // Selected node for drawer
      selectedNode: null,

      // Label of a node to auto-select after the next loadVersion call.
      // Set by useAddNodeOptimistic when a node is added on an active version
      // (active→draft path) so the drawer opens once loadVersion syncs IDs.
      _pendingNodeSelection: null,
      setPendingNodeSelection: (label) =>
        set({ _pendingNodeSelection: label }, false, "setPendingNodeSelection"),

      // When true, a draft creation POST is in-flight or debouncing.
      // Used to block tab navigation during optimistic active→draft transitions.
      _isDraftCreating: false,

      // When true, AgentBuilder's useEffect skips the next loadVersion call.
      // Set during optimistic active→draft transitions to prevent loadVersion
      // from overwriting the optimistic state when cache-seeded data arrives.
      _skipNextLoadVersion: false,

      // Flag to auto-run workflow after save completes
      pendingRunAfterSave: false,
      setPendingRunAfterSave: (pending) =>
        set({ pendingRunAfterSave: pending }, false, "setPendingRunAfterSave"),

      // Track when a connection is being made
      // connectingFromNodeId: node ID where user started connecting (source)
      // isConnecting: general flag that connection is in progress
      connectingFromNodeId: null,
      isConnecting: false,
      setConnectingFromNodeId: (nodeId) =>
        set({ connectingFromNodeId: nodeId }, false, "setConnectingFromNodeId"),
      setIsConnecting: (isConnecting) =>
        set({ isConnecting }, false, "setIsConnecting"),

      openSaveAgentDialog: false,
      setOpenSaveAgentDialog: (open) =>
        set({ openSaveAgentDialog: open }, false, "setOpenSaveAgentDialog"),

      // Draft confirmation dialog state
      draftConfirmDialog: {
        open: false,
        callback: null,
        onCancel: null,
        message: null,
      },
      openDraftConfirmDialog: (callback, message = null, onCancel = null) =>
        set(
          {
            draftConfirmDialog: {
              open: true,
              callback,
              onCancel,
              message,
            },
          },
          false,
          "openDraftConfirmDialog",
        ),
      closeDraftConfirmDialog: () => {
        const { draftConfirmDialog } = get();
        draftConfirmDialog.onCancel?.();
        set(
          {
            draftConfirmDialog: {
              open: false,
              callback: null,
              onCancel: null,
              message: null,
            },
          },
          false,
          "closeDraftConfirmDialog",
        );
      },
      confirmDraftDialog: () => {
        const { draftConfirmDialog } = get();
        if (draftConfirmDialog.callback) {
          draftConfirmDialog.callback();
        }
        set(
          {
            draftConfirmDialog: {
              open: false,
              callback: null,
              onCancel: null,
              message: null,
            },
          },
          false,
          "confirmDraftDialog",
        );
      },

      // Tracks whether the NodeDrawer form has unsaved changes.
      // Used by ensureDraft() to show a discard dialog before creating a draft,
      // and by BuilderActions to warn before running with stale config.
      _isNodeFormDirty: false,
      setNodeFormDirty: (dirty) => set({ _isNodeFormDirty: dirty }),

      setSelectedNode: (node) => {
        const isRunning = useWorkflowRunStore.getState().isRunning;
        if (isRunning) {
          return;
        }
        set({ selectedNode: node }, false, "setSelectedNode");
      },
      clearSelectedNode: () =>
        set({ selectedNode: null }, false, "clearSelectedNode"),

      // Validation error state for nodes
      validationErrorNodeIds: [],
      setValidationErrorNodeIds: (nodeIds) =>
        set(
          { validationErrorNodeIds: nodeIds },
          false,
          "setValidationErrorNodeIds",
        ),
      clearValidationErrors: () =>
        set({ validationErrorNodeIds: [] }, false, "clearValidationErrors"),
      clearValidationErrorNode: (nodeId) =>
        set(
          {
            validationErrorNodeIds: get().validationErrorNodeIds.filter(
              (id) => id !== nodeId,
            ),
          },
          false,
          "clearValidationErrorNode",
        ),

      // Execution state for nodes (idle, running, completed, error)
      nodeExecutionStates: {},
      setNodeExecutionState: (nodeId, state) =>
        set(
          {
            nodeExecutionStates: {
              ...get().nodeExecutionStates,
              [nodeId]: state,
            },
          },
          false,
          "setNodeExecutionState",
        ),
      setNodeExecutionStates: (statesMap) =>
        set(
          { nodeExecutionStates: statesMap },
          false,
          "setNodeExecutionStates",
        ),

      // Execution state for edges (idle, active, completed)
      edgeExecutionStates: {},
      setEdgeExecutionState: (edgeId, state) =>
        set(
          {
            edgeExecutionStates: {
              ...get().edgeExecutionStates,
              [edgeId]: state,
            },
          },
          false,
          "setEdgeExecutionState",
        ),
      setEdgeExecutionStates: (statesMap) =>
        set(
          { edgeExecutionStates: statesMap },
          false,
          "setEdgeExecutionStates",
        ),

      // Clear all execution states
      clearAllExecutionStates: () =>
        set(
          {
            nodeExecutionStates: {},
            edgeExecutionStates: {},
            selectedNode: null,
          },
          false,
          "clearAllExecutionStates",
        ),

      // Compute new node data without writing to the store.
      // Used in the active-version path to build a draft creation payload
      // without triggering a render before the user confirms.
      computeNewNodeData: (
        type,
        position,
        sourceNodeId,
        nodeTemplateId,
        name,
        config,
      ) => {
        const { nodes } = get();

        const nodeId = crypto.randomUUID();
        const baseName = _.snakeCase(
          name || getNodeLabel(generateNodeId(type, nodes)),
        );
        const label = generateUniqueLabel(baseName, nodes, name ? "_v" : "_");

        // Store-generated config (always persisted)
        // prompt_version_id is always null here — synced from backend via getNodeDetail
        const baseConfig =
          type === NODE_TYPES.LLM_PROMPT
            ? {
                prompt_template_id: config?.prompt_template_id ?? null,
                prompt_version_id: null,
              }
            : type === NODE_TYPES.CODE_EXECUTION
              ? { ...CODE_EXECUTION_DEFAULT_CONFIG, ...config }
              : {};

        // Caller-provided config is transient — only for form population, not persisted
        const initialConfig =
          config && Object.keys(config).length > 0 ? config : null;

        const isAgent = type === NODE_TYPES.AGENT;
        const isCodeExecution = type === NODE_TYPES.CODE_EXECUTION;
        const ports = isAgent
          ? undefined
          : isCodeExecution
            ? CODE_EXECUTION_PORTS.map((port) => ({
                ...port,
                id: crypto.randomUUID(),
              }))
            : [
                {
                  id: crypto.randomUUID(),
                  key: PORT_KEYS.RESPONSE,
                  display_name: generateOutputLabel(nodes),
                  direction: PORT_DIRECTION.OUTPUT,
                  data_schema: { type: "string" },
                  required: true,
                },
              ];

        const newNode = {
          id: nodeId,
          type,
          position: position || calculateNodePosition(nodes),
          data: {
            label,
            ...(ports && { ports }),
            ...(nodeTemplateId && { node_template_id: nodeTemplateId }),
            ...(Object.keys(baseConfig).length > 0 && { config: baseConfig }),
            ...(initialConfig && { _initialConfig: initialConfig }),
          },
        };

        const edgeId = sourceNodeId ? crypto.randomUUID() : null;
        const newEdge = sourceNodeId
          ? { id: edgeId, source: sourceNodeId, target: nodeId }
          : null;

        return {
          nodeId,
          edgeId,
          newNode,
          newEdge,
          position: newNode.position,
          ports,
          config: baseConfig,
        };
      },

      // Add an optimistic node immediately (before API returns).
      // Delegates computation to computeNewNodeData then writes to store.
      addOptimisticNode: (
        type,
        position,
        sourceNodeId,
        nodeTemplateId,
        name,
        config,
      ) => {
        if (useWorkflowRunStore.getState().isRunning) return null;
        const computed = get().computeNewNodeData(
          type,
          position,
          sourceNodeId,
          nodeTemplateId,
          name,
          config,
        );
        const { nodes, edges } = get();

        set(
          {
            nodes: [...nodes, computed.newNode],
            edges: computed.newEdge ? [...edges, computed.newEdge] : edges,
          },
          false,
          "addOptimisticNode",
        );

        return {
          nodeId: computed.nodeId,
          edgeId: computed.edgeId,
          position: computed.position,
          ports: computed.ports,
          config: computed.config,
          label: computed.newNode.data.label,
        };
      },

      // Replace a dummy edge ID with the real one returned by the backend.
      updateEdgeId: (oldId, newId) => {
        const { edges } = get();
        set(
          {
            edges: edges.map((e) => (e.id === oldId ? { ...e, id: newId } : e)),
          },
          false,
          "updateEdgeId",
        );
      },

      // Remove an optimistic node on API failure (rollback)
      removeOptimisticNode: (nodeId) => {
        const { nodes, edges, selectedNode } = get();
        set(
          {
            nodes: nodes.filter((n) => n.id !== nodeId),
            edges: edges.filter(
              (e) => e.source !== nodeId && e.target !== nodeId,
            ),
            selectedNode: selectedNode?.id === nodeId ? null : selectedNode,
          },
          false,
          "removeOptimisticNode",
        );
      },

      // Handle node changes (position, selection, etc.)
      onNodesChange: (changes) => {
        const { nodes, edges, selectedNode } = get();

        // Block remove changes while workflow is running
        const effectiveChanges = useWorkflowRunStore.getState().isRunning
          ? changes.filter((change) => change.type !== "remove")
          : changes;

        // Find removed node IDs
        const removedNodeIds = effectiveChanges
          .filter((change) => change.type === "remove")
          .map((change) => change.id);

        // Apply node changes
        const newNodes = applyNodeChanges(effectiveChanges, nodes);

        // Clean up edges connected to removed nodes
        let newEdges = edges;
        if (removedNodeIds.length > 0) {
          newEdges = edges.filter(
            (edge) =>
              !removedNodeIds.includes(edge.source) &&
              !removedNodeIds.includes(edge.target),
          );
        }

        // Clear selected node if it was deleted
        const newSelectedNode =
          selectedNode && removedNodeIds.includes(selectedNode.id)
            ? null
            : selectedNode;

        set(
          {
            nodes: newNodes,
            edges: newEdges,
            selectedNode: newSelectedNode,
          },
          false,
          "onNodesChange",
        );
      },

      // Handle edge changes
      onEdgesChange: (changes) => {
        const { nodes, edges } = get();
        // Block remove changes while workflow is running
        const changesAfterRunCheck = useWorkflowRunStore.getState().isRunning
          ? changes.filter((change) => change.type !== "remove")
          : changes;
        // Filter out remove changes for non-deletable edges, but allow if node was deleted
        const filteredChanges = changesAfterRunCheck.filter((change) => {
          if (change.type === "remove") {
            const edge = edges.find((e) => e.id === change.id);
            if (edge?.deletable === false) {
              // Allow deletion if the source or target node no longer exists
              const sourceExists = nodes.some((n) => n.id === edge.source);
              const targetExists = nodes.some((n) => n.id === edge.target);
              if (sourceExists && targetExists) {
                return false; // Don't allow manual deletion if both nodes exist
              }
            }
          }
          return true;
        });

        const newEdges = applyEdgeChanges(filteredChanges, edges);
        set({ edges: newEdges }, false, "onEdgesChange");
      },

      // Handle new connections (node-to-node, many-to-one allowed)
      onConnect: (connection) => {
        if (useWorkflowRunStore.getState().isRunning) return;
        const { edges } = get();

        // Build a clean node-to-node edge (ignore handle IDs from React Flow)
        const edgeId = connection.id || crypto.randomUUID();
        const newEdge = {
          id: edgeId,
          source: connection.source,
          target: connection.target,
        };

        const newEdges = addEdge(newEdge, edges);

        set({ edges: newEdges }, false, "onConnect");
      },

      // Delete a node (blocked while workflow is running)
      deleteNode: (nodeId) => {
        if (useWorkflowRunStore.getState().isRunning) return;
        const { nodes, edges, selectedNode } = get();
        const newNodes = nodes.filter((n) => n.id !== nodeId);
        const newEdges = edges.filter(
          (e) => e.source !== nodeId && e.target !== nodeId,
        );

        // Clear selected node if it was deleted
        const newSelectedNode =
          selectedNode?.id === nodeId ? null : selectedNode;

        set(
          {
            nodes: newNodes,
            edges: newEdges,
            selectedNode: newSelectedNode,
          },
          false,
          "deleteNode",
        );
      },

      // Update node data by nodeId
      updateNodeData: (nodeId, newData) => {
        const { nodes } = get();
        const updatedNodes = nodes.map((node) => {
          if (node.id === nodeId) {
            return {
              ...node,
              data: {
                ...node.data,
                ...newData,
              },
            };
          }
          return node;
        });

        set({ nodes: updatedNodes }, false, "updateNodeData");
      },

      // Rename the output port display_name for a node and propagate to downstream variables
      renameOutputPort: (nodeId, newLabel) => {
        const { nodes } = get();
        const node = nodes.find((n) => n.id === nodeId);
        const oldOutputLabel = getOutputPortLabels(node)[0];
        const nodeLabel = node?.data?.label || nodeId;

        const updatedNodes = nodes.map((n) => {
          if (n.id !== nodeId) return n;
          return {
            ...n,
            data: {
              ...n.data,
              ports: (n.data?.ports || []).map((p) =>
                p.direction === PORT_DIRECTION.OUTPUT
                  ? { ...p, display_name: newLabel }
                  : p,
              ),
            },
          };
        });
        set({ nodes: updatedNodes }, false, "renameOutputPort");

        // Propagate variable references in downstream nodes
        startTransition(() => {
          get().propagateVariableRename(
            nodeId,
            nodeLabel,
            nodeLabel,
            oldOutputLabel,
            newLabel,
          );
        });
      },

      // Propagate variable renames to all downstream nodes' message content
      propagateVariableRename: (
        nodeId,
        oldLabel,
        newLabel,
        oldOutputLabel,
        newOutputLabel,
      ) => {
        const oldPrefix = `${oldLabel}.${oldOutputLabel}`;
        const newPrefix = `${newLabel}.${newOutputLabel}`;
        if (oldPrefix === newPrefix) return;

        const { edges, nodes } = get();
        const downstreamIds = new Set(
          edges.filter((e) => e.source === nodeId).map((e) => e.target),
        );
        if (downstreamIds.size === 0) return;

        let changed = false;
        const updatedNodes = nodes.map((node) => {
          if (!downstreamIds.has(node.id)) return node;

          let nodeChanged = false;
          let updatedConfig = node.data?.config;

          // Update message content in prompt nodes
          const messages = node.data?.config?.messages;
          if (messages?.length) {
            const updatedMessages = messages.map((msg) => {
              if (!msg.content?.length) return msg;
              const updatedContent = msg.content.map((block) => {
                if (block.type !== "text" || !block.text) return block;
                // Replace exact match {{oldPrefix}} and prefix match {{oldPrefix.
                const updated = block.text
                  .replaceAll(`{{${oldPrefix}}}`, `{{${newPrefix}}}`)
                  .replaceAll(`{{${oldPrefix}.`, `{{${newPrefix}.`);
                if (updated !== block.text) {
                  nodeChanged = true;
                  return { ...block, text: updated };
                }
                return block;
              });
              return nodeChanged ? { ...msg, content: updatedContent } : msg;
            });
            if (nodeChanged) {
              updatedConfig = { ...updatedConfig, messages: updatedMessages };
            }
          }

          // Update inputMappings in agent nodes
          const inputMappings = node.data?.config?.payload?.inputMappings;
          if (Array.isArray(inputMappings) && inputMappings.length > 0) {
            let mappingChanged = false;
            const updatedMappings = inputMappings.map(({ key, value }) => {
              if (!value) return { key, value };
              if (value === oldPrefix) {
                mappingChanged = true;
                return { key, value: newPrefix };
              } else if (value.startsWith(`${oldPrefix}.`)) {
                mappingChanged = true;
                return { key, value: value.replace(oldPrefix, newPrefix) };
              }
              return { key, value };
            });
            if (mappingChanged) {
              nodeChanged = true;
              updatedConfig = {
                ...updatedConfig,
                payload: {
                  ...updatedConfig?.payload,
                  inputMappings: updatedMappings,
                },
              };
            }
          }

          if (nodeChanged) {
            changed = true;
            return {
              ...node,
              data: {
                ...node.data,
                config: updatedConfig,
              },
            };
          }
          return node;
        });

        if (changed) {
          set({ nodes: updatedNodes }, false, "propagateVariableRename");
        }
      },

      // Get node by id
      getNodeById: (nodeId) => {
        const { nodes } = get();
        return nodes.find((node) => node.id === nodeId);
      },

      // Set graph data from template
      setGraphData: (nodes, edges) => {
        set({ nodes, edges, selectedNode: null }, false, "setGraphData");
      },

      // Load version from API response into XYFlow state
      loadVersion: (apiVersionData) => {
        const { nodes: xyNodes, edges: xyEdges } =
          parseVersionResponse(apiVersionData);
        const pendingLabel = get()._pendingNodeSelection;
        const selectedNode = pendingLabel
          ? xyNodes.find((n) => n.data?.label === pendingLabel) || null
          : null;
        if (xyNodes.length === 0) {
          set(
            {
              nodes: [],
              edges: [],
              selectedNode: null,
              _pendingNodeSelection: null,
              isGraphReady: true,
            },
            false,
            "loadVersion",
          );
          return;
        }
        set(
          {
            nodes: xyNodes,
            edges: xyEdges,
            selectedNode,
            _pendingNodeSelection: null,
            isGraphReady: true,
          },
          false,
          "loadVersion",
        );
      },

      /**
       * Remap all node IDs to fresh UUIDs for draft creation.
       * Updates nodes, edges (source/target), and selectedNode in-place.
       * Returns the idMap and remapped data for building the API payload.
       */
      remapNodeIds: () => {
        const { nodes, edges, selectedNode } = get();
        const idMap = {};
        nodes.forEach((n) => {
          idMap[n.id] = crypto.randomUUID();
        });

        const remappedNodes = nodes.map((n) => ({ ...n, id: idMap[n.id] }));
        const remappedEdges = edges.map((e) => ({
          ...e,
          source: idMap[e.source] ?? e.source,
          target: idMap[e.target] ?? e.target,
        }));

        const newSelectedNode = selectedNode
          ? remappedNodes.find((n) => n.id === idMap[selectedNode.id]) || null
          : null;

        set(
          {
            nodes: remappedNodes,
            edges: remappedEdges,
            selectedNode: newSelectedNode,
          },
          false,
          "remapNodeIds",
        );

        return { idMap, remappedNodes, remappedEdges };
      },

      /**
       * Sync edge IDs from the backend response after draft creation.
       * The POST doesn't include edge IDs, so the backend assigns new ones.
       * Match by source+target to update store edges with backend-assigned IDs.
       *
       * Note: The `${src}->${tgt}` key is unique because only one connection
       * is allowed between any two nodes (addEdge deduplicates, handles stripped).
       */
      syncEdgeIdsFromResponse: (nodeConnections) => {
        if (!nodeConnections?.length) return;
        const lookup = new Map();
        nodeConnections.forEach((nc) => {
          const src = nc.sourceNodeId;
          const tgt = nc.targetNodeId;
          if (src && tgt && nc.id) lookup.set(`${src}->${tgt}`, nc.id);
        });
        const { edges } = get();
        const updated = edges.map((e) => {
          const key = `${e.source}->${e.target}`;
          const backendId = lookup.get(key);
          return backendId ? { ...e, id: backendId } : e;
        });
        set({ edges: updated }, false, "syncEdgeIdsFromResponse");
      },

      /**
       * Restore a graph snapshot (for rollback on failed draft creation).
       * Unlike setGraphData, preserves selectedNode from the snapshot.
       */
      restoreGraphSnapshot: (snapshot) => {
        set(
          {
            nodes: snapshot.nodes,
            edges: snapshot.edges,
            selectedNode: snapshot.selectedNode,
          },
          false,
          "restoreGraphSnapshot",
        );
      },

      // Reset store
      reset: () => {
        set(store.getInitialState());
      },
    }),
    {
      name: "AgentPlaygroundStore",
      enabled: CURRENT_ENVIRONMENT !== "production",
    },
  ),
);

export const useAgentPlaygroundStoreShallow = (fun) =>
  useAgentPlaygroundStore(useShallow(fun));

export const resetAgentPlaygroundStore = () => {
  useAgentPlaygroundStore.getState().reset();
};

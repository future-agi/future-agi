import { create } from "zustand";
import {
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  MarkerType,
} from "@xyflow/react";
import { getRandomId } from "src/utils/utils";
import logger from "src/utils/logger";

export const NODE_TYPES = {
  CONVERSATION: "conversation",
  END: "end",
  TRANSFER: "transfer",
  END_CHAT: "endChat",
  TRANSFER_CHAT: "transferChat",
};

export const getDefaultNodeData = (nodeType, id, extraData = {}) => {
  const randomId = id ?? getRandomId();
  switch (nodeType) {
    case NODE_TYPES.CONVERSATION:
      return {
        prompt: "",
        isStart: false,
        isGlobal: false,
        name: `Conversation_${randomId}`,
        ...extraData,
      };
    case NODE_TYPES.END:
      return {
        name: `End_call_${randomId}`,
        prompt: "",
        ...extraData,
      };
    case NODE_TYPES.TRANSFER:
      return {
        name: `Transfer_call_${randomId}`,
        prompt: "",
        ...extraData,
      };
    case NODE_TYPES.END_CHAT:
      return {
        name: `End_chat_${randomId}`,
        prompt: "",
        ...extraData,
      };
    case NODE_TYPES.TRANSFER_CHAT:
      return {
        name: `Transfer_chat_${randomId}`,
        prompt: "",
        ...extraData,
      };
  }
};

const getInitialNodes = () => {
  const randomId = getRandomId();

  return [
    {
      id: `Conversation_${randomId}`,
      type: NODE_TYPES.CONVERSATION,
      position: { x: 250, y: 50 },
      data: {
        ...getDefaultNodeData(NODE_TYPES.CONVERSATION, randomId),
        isStart: true,
      },
    },
  ];
};

const initialEdges = [];

export const useGraphStore = create((set, get) => ({
  nodes: getInitialNodes(),
  edges: initialEdges,

  onNodesChange: (changes) => {
    set({
      nodes: applyNodeChanges(changes, get().nodes),
    });
  },

  onEdgesChange: (changes) => {
    set({
      edges: applyEdgeChanges(changes, get().edges),
    });
  },

  onConnect: (connection) => {
    const edge = {
      ...connection,
      type: "condition",
      markerEnd: {
        type: MarkerType.ArrowClosed,
      },
      data: {
        condition: "Default condition",
      },
    };

    set({
      edges: addEdge(edge, get().edges),
    });
  },

  addNode: (nodeType, position, editMode) => {
    const randomId = getRandomId();
    const newNode = {
      id: `${getGetNodeNameByType(nodeType)}_${randomId}`,
      type: nodeType,
      position,
      data: {
        ...getDefaultNodeData(nodeType, randomId, { editMode }),
      },
    };

    set({
      nodes: [...get().nodes, newNode],
    });
  },

  updateNodeData: (nodeId, newData) => {
    set({
      nodes: get().nodes.map((node) =>
        node.id === nodeId
          ? { ...node, data: { ...node.data, ...newData } }
          : node,
      ),
    });
  },
  updateNode: (nodeId, newData) => {
    set({
      nodes: get().nodes.map((node) =>
        node.id === nodeId
          ? typeof newData === "function"
            ? newData(node)
            : { ...node, ...newData }
          : node,
      ),
    });
  },

  deleteNode: (nodeId) => {
    const { activeNodeId } = get();

    set({
      nodes: get().nodes.filter((node) => node.id !== nodeId),
      edges: get().edges.filter(
        (edge) => edge.source !== nodeId && edge.target !== nodeId,
      ),
      activeNodeId: activeNodeId === nodeId ? null : activeNodeId,
    });
  },

  updateEdgeData: (edgeId, newData) => {
    set({
      edges: get().edges.map((edge) =>
        edge.id === edgeId
          ? {
              ...edge,
              data:
                typeof newData === "function"
                  ? newData(edge.data)
                  : { ...edge.data, ...newData },
            }
          : edge,
      ),
    });
  },

  clearGraph: () => {
    set({
      nodes: [],
      edges: [],
    });
  },

  resetGraph: (newNodes, newEdges) => {
    logger.debug("Reset graph", { newNodes, newEdges });
    set({
      nodes: newNodes ?? getInitialNodes(),
      edges: newEdges ?? initialEdges,
    });
  },
  duplicateNode: (nodeId) => {
    const node = get().nodes.find((node) => node.id === nodeId);
    const nodeType = node.type;
    const newNodeName = `${getGetNodeNameByType(nodeType)}_${getRandomId()}`;
    const newNode = {
      id: newNodeName,
      type: node.type,
      position: { x: node.position.x + 200, y: node.position.y + 200 },
      data: {
        ...node.data,
        name: newNodeName,
      },
      // Explicitly reset React Flow properties that might cause grouping
      selected: false,
      dragging: false,
      // Only copy essential properties, not internal React Flow state
    };
    set({
      nodes: [...get().nodes, newNode],
    });
  },
  // Active selection tracking (either node or edge, not both)
  activeNodeId: null,
  activeEdgeId: null,

  setActiveNode: (nodeId) => {
    set({
      activeNodeId: nodeId || null,
      activeEdgeId: null, // Clear active edge when selecting node
    });
  },

  setActiveEdge: (edgeId) => {
    set({
      activeEdgeId: edgeId || null,
      activeNodeId: null, // Clear active node when selecting edge
    });
  },

  clearActiveSelection: () => {
    set({
      activeNodeId: null,
      activeEdgeId: null,
    });
  },

  reset: () => {
    set({
      nodes: getInitialNodes(),
      edges: initialEdges,
      activeNodeId: null,
      activeEdgeId: null,
    });
  },
}));

export const resetGraphStore = () => {
  useGraphStore.getState().reset();
};

export function getNodeLabel(nodeType) {
  switch (nodeType) {
    case NODE_TYPES.CONVERSATION:
      return "Conversation";
    case NODE_TYPES.END:
      return "End Call";
    case NODE_TYPES.TRANSFER:
      return "Transfer Call";
    case NODE_TYPES.END_CHAT:
      return "End Chat";
    case NODE_TYPES.TRANSFER_CHAT:
      return "Transfer Chat";
    default:
      return "Unknown Node";
  }
}

export function getNodeDescription(nodeType) {
  switch (nodeType) {
    case NODE_TYPES.CONVERSATION:
      return "Handle conversation with user";
    case NODE_TYPES.END:
      return "End the call session";
    case NODE_TYPES.TRANSFER:
      return "Transfer to another agent";
    default:
      return "Unknown node type";
  }
}

export const getGetNodeNameByType = (nodeType) => {
  switch (nodeType) {
    case NODE_TYPES.CONVERSATION:
      return "Conversation";
    case NODE_TYPES.END:
      return "End_call";
    case NODE_TYPES.TRANSFER:
      return "Transfer_call";
    case NODE_TYPES.END_CHAT:
      return "End_chat";
    case NODE_TYPES.TRANSFER_CHAT:
      return "Transfer_chat";
    default:
      return "Unknown";
  }
};

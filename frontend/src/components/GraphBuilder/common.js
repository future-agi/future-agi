import { getRandomId } from "src/utils/utils";
import { NODE_TYPES } from "./store/graphStore";
import Dagre from "@dagrejs/dagre";
import { stratify, tree } from "d3-hierarchy";
import { AGENT_TYPES } from "src/sections/agents/constants";

export const GRAPH_NODES = [
  {
    type: "conversation",
    name: "Conversation",
    description: "Start a conversation with the user",
    icon: "/assets/icons/navbar/ic_prompt.svg",
    color: "primary.main",
    backgroundColor: "action.hover",
    agentType: "all",
  },
  {
    type: "end",
    name: "End call",
    description: "Split flow based on conditions or logic",
    icon: "/assets/icons/components/ic_end_call.svg",
    color: "red.600",
    backgroundColor: "red.o5",
    agentType: AGENT_TYPES.VOICE,
  },
  {
    type: "transfer",
    name: "Transfer call",
    description: "Combine inputs from multiple paths",
    icon: "/assets/icons/components/ic_transfer_call.svg",
    color: "orange.600",
    backgroundColor: "orange.o5",
    agentType: AGENT_TYPES.VOICE,
  },
  {
    type: "endChat",
    name: "End chat",
    description: "End the chat session",
    icon: "/assets/icons/ic_end_chat.svg",
    color: "red.600",
    backgroundColor: "red.o5",
    agentType: AGENT_TYPES.CHAT,
  },
  {
    type: "transferChat",
    name: "Transfer chat",
    description: "Transfer chat to another agent",
    icon: "/assets/icons/ic_transfer_chat.svg",
    color: "orange.600",
    backgroundColor: "orange.o5",
    agentType: AGENT_TYPES.CHAT,
  },
];

const getTypeFromNodeData = (node) => {
  if (node?.type === "tool") {
    if (node?.tool?.type === "endCall") {
      return NODE_TYPES.END;
    } else if (node?.tool?.type === "transferCall") {
      return NODE_TYPES.TRANSFER;
    } else if (node?.tool?.type === "endChat") {
      return NODE_TYPES.END_CHAT;
    } else if (node?.tool?.type === "transferChat") {
      return NODE_TYPES.TRANSFER_CHAT;
    }
  } else if (node?.type === "conversation") {
    return NODE_TYPES.CONVERSATION;
  }
};

const extractNodeData = (node, type) => {
  if (type === NODE_TYPES.CONVERSATION) {
    return {
      isStart: node?.isStart ?? false,
      isGlobal: node?.isGlobal ?? false,
      prompt: node?.prompt ?? "",
      name: node?.name ?? "",
    };
  } else if (type === NODE_TYPES.END) {
    // I am just extracting the info i need for end call
    return {
      prompt: node?.tool?.messages?.[0]?.content ?? "",
      name: node?.name ?? "",
    };
  } else if (type === NODE_TYPES.TRANSFER) {
    // I am just extracting the info i need for transfer call
    return {
      prompt: node?.tool?.messages?.[0]?.content ?? "",
      name: node?.name ?? "",
    };
  } else if (type === NODE_TYPES.END_CHAT) {
    return {
      prompt: node?.tool?.messages?.[0]?.content ?? "",
      name: node?.name ?? "",
    };
  } else if (type === NODE_TYPES.TRANSFER_CHAT) {
    return {
      prompt: node?.tool?.messages?.[0]?.content ?? "",
      name: node?.name ?? "",
    };
  }
};

const extractEdgeData = (edge) => {
  return {
    prompt: edge?.condition?.prompt ?? "",
  };
};

// ⚠️ NOTE: transforms node/edge, if you change what fields we put here you gotta update normalizeGraph too
// src/sections/scenarios/scenario-detail/common.js
export const transformNode = (node, extraData = {}) => {
  const nodeType = getTypeFromNodeData(node);
  const data = { ...extractNodeData(node, nodeType), ...extraData };
  return {
    id: node?.name ?? getRandomId(),
    type: nodeType,
    position: node?.metadata?.position
      ? node?.metadata?.position
      : { x: 250, y: 50 },
    data,
    ...(data.isStart && { deletable: false }),
  };
};

// ⚠️ NOTE: transforms node/edge, if you change what fields we put here you gotta update normalizeGraph too
// src/sections/scenarios/scenario-detail/common.js
export const transformEdge = (edge, extraData = {}) => {
  return {
    id: getRandomId(),
    type: "condition",
    source: edge?.from,
    target: edge?.to,
    data: { ...extractEdgeData(edge), ...extraData },
  };
};

export const dagreTransformAndLayout = (
  incomingNodes,
  incomingEdges,
  nodeExtraData = {},
  edgeExtraData = {},
  options = {},
) => {
  const {
    nodeWidth = 400,
    nodeHeight = 200,
    ranksep = 200,
    edgesep = 200,
    nodesep = 100,
  } = options;
  const g = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  g.setGraph({
    rankdir: "TB",
    ranksep,
    edgesep,
    nodesep,
  });

  const nodes = incomingNodes?.map((node) => {
    let extraDataToUse = nodeExtraData;
    if (typeof nodeExtraData === "function") {
      extraDataToUse = nodeExtraData(node);
    }
    const transformedNode = transformNode(node, extraDataToUse);
    g.setNode(transformedNode.id, {
      width: nodeWidth,
      height: nodeHeight,
    });

    return transformedNode;
  });
  const edges = incomingEdges?.map((edge) => {
    let extraDataToUse = edgeExtraData;
    if (typeof edgeExtraData === "function") {
      extraDataToUse = edgeExtraData(edge);
    }
    const transformedEdge = transformEdge(edge, extraDataToUse);
    g.setEdge(transformedEdge.source, transformedEdge.target, {
      width: 300,
    });
    return transformedEdge;
  });
  Dagre.layout(g);
  const newNodes = nodes.map((node) => {
    const position = g.node(node.id);

    return {
      ...node,
      position: { x: position.x, y: position.y },
    };
  });

  return { nodes: newNodes, edges: edges };
};

export const d3TransformAndLayout = (incomingNodes, incomingEdges) => {
  const g = tree()
    .nodeSize([500, 300]) // Increased spacing: [horizontal, vertical]
    .separation((a, b) => {
      // Increase separation between nodes at same level
      return a.parent === b.parent ? 2 : 3;
    });

  const nodes = incomingNodes?.map(transformNode);
  const edges = incomingEdges?.map(transformEdge);

  const hierarchy = stratify()
    .id((node) => node.id)
    .parentId((node) => edges.find((edge) => edge.target === node.id)?.source);

  const root = hierarchy(nodes);
  const layout = g(root);

  // Center the tree and add more spacing
  const descendants = layout.descendants();
  const minX = Math.min(...descendants.map((d) => d.x));
  const maxX = Math.max(...descendants.map((d) => d.x));
  const treeWidth = maxX - minX;
  const centerOffset = -treeWidth / 2;

  return {
    nodes: descendants.map((node) => ({
      ...node.data,
      position: {
        x: node.x + centerOffset,
        y: node.y + 100, // Add top padding
      },
    })),
    edges,
  };
};

const getBaseColorsBasedOnNodeType = (nodeType) => {
  if (nodeType === NODE_TYPES.CONVERSATION) {
    return {
      backgroundColor: "divider",
      borderColor: "primary.main",
      hoverBorderColor: "primary.main",
    };
  } else if (nodeType === NODE_TYPES.END) {
    return {
      backgroundColor: "background.paper",
      borderColor: "red.600",
      hoverBorderColor: "red.600",
    };
  } else if (nodeType === NODE_TYPES.TRANSFER) {
    return {
      backgroundColor: "background.paper",
      borderColor: "orange.600",
      hoverBorderColor: "orange.600",
    };
  } else if (nodeType === NODE_TYPES.END_CHAT) {
    return {
      backgroundColor: "background.paper",
      borderColor: "red.600",
      hoverBorderColor: "red.600",
    };
  } else if (nodeType === NODE_TYPES.TRANSFER_CHAT) {
    return {
      backgroundColor: "background.paper",
      borderColor: "orange.600",
      hoverBorderColor: "orange.600",
    };
  }
};

export const getNodeColors = (isStart, isHighlighted, isActive, nodeType) => {
  const baseColors = getBaseColorsBasedOnNodeType(nodeType);
  if (isHighlighted === "success") {
    return {
      backgroundColor: "green.o10",
      borderColor: "green.600",
      hoverBorderColor: "green.600",
    };
  } else if (isHighlighted === "error") {
    return {
      backgroundColor: "red.o10",
      borderColor: "red.600",
      hoverBorderColor: "red.600",
    };
  } else {
    if (isStart) {
      return {
        backgroundColor: "action.selected",
        borderColor: "primary.main",
        hoverBorderColor: "primary.main",
      };
    } else if (isActive) {
      return {
        backgroundColor: "background.paper",
        borderColor: baseColors.borderColor,
        hoverBorderColor: baseColors.hoverBorderColor,
      };
    } else {
      return {
        backgroundColor: "background.paper",
        borderColor: "divider",
        hoverBorderColor: baseColors.hoverBorderColor,
      };
    }
  }
};

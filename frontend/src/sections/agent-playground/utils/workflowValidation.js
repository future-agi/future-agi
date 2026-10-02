import { z } from "zod";
import { NODE_TYPES } from "./constants";

// Schema for validating a single workflow node
const workflowNodeSchema = z.object({
  id: z.string(),
  type: z.string(),
  data: z
    .object({
      config: z
        .record(z.any())
        .refine((c) => Object.keys(c).length > 0, "Node data must be saved"),
    })
    .passthrough(),
});

// Get all connected nodes in the workflow graph
export function getConnectedNodes(nodes, edges) {
  if (nodes.length === 0) return [];

  // Find root nodes (no incoming edges) as entry points
  const targetIds = new Set(edges.map((e) => e.target));
  const rootNodes = nodes.filter((n) => !targetIds.has(n.id));

  // If no root nodes found, return all nodes (all connected in a cycle — caught by cycle detection)
  if (rootNodes.length === 0) return nodes;

  // BFS from all root nodes to find connected nodes
  const connectedNodeIds = new Set();
  const queue = rootNodes.map((n) => n.id);
  queue.forEach((id) => connectedNodeIds.add(id));

  while (queue.length > 0) {
    const currentId = queue.shift();
    const outgoingEdges = edges.filter((e) => e.source === currentId);

    for (const edge of outgoingEdges) {
      if (!connectedNodeIds.has(edge.target)) {
        connectedNodeIds.add(edge.target);
        queue.push(edge.target);
      }
    }
  }

  return nodes.filter((n) => connectedNodeIds.has(n.id));
}

// Validate a single node
export function validateNode(node) {
  const result = workflowNodeSchema.safeParse(node);

  if (!result.success) {
    return {
      valid: false,
      node_id: node.id,
      errors: result.error.errors.map((e) => e.message),
    };
  }

  return { valid: true, node_id: node.id, errors: [] };
}

// Validate all connected nodes in the workflow
export function validateWorkflow(nodes, edges) {
  const connectedNodes = getConnectedNodes(nodes, edges);

  // Check if there are any nodes to run
  if (connectedNodes.length === 0) {
    return {
      valid: false,
      errors: [
        {
          nodeId: null,
          message: "No nodes connected to the workflow",
        },
      ],
      invalidNodeIds: [],
    };
  }

  const validationResults = connectedNodes.map(validateNode);
  const invalidNodes = validationResults.filter((r) => !r.valid);

  if (invalidNodes.length > 0) {
    return {
      valid: false,
      errors: invalidNodes.map((n) => ({
        nodeId: n.node_id,
        message: "Node not configured",
      })),
      invalidNodeIds: invalidNodes.map((n) => n.node_id),
    };
  }

  return {
    valid: true,
    errors: [],
    invalidNodeIds: [],
  };
}

// Custom error class for workflow validation
export class WorkflowValidationError extends Error {
  constructor(validationResult) {
    const message =
      validationResult.errors.length > 0
        ? validationResult.errors[0].message
        : "Workflow validation failed";
    super(message);
    this.name = "WorkflowValidationError";
    this.validationResult = validationResult;
    this.invalidNodeIds = validationResult.invalidNodeIds;
  }
}

// Check if a user-role message has non-empty content
function hasValidUserContent(message) {
  if (message.role !== "user") return true;
  if (!Array.isArray(message.content) || message.content.length === 0)
    return false;
  return message.content.some((item) => {
    if (item.type === "text") return item.text?.trim().length > 0;
    if (item.type === "image_url")
      return item.image_url?.url?.trim().length > 0;
    if (item.type === "pdf_url") return item.pdf_url?.url?.trim().length > 0;
    if (item.type === "audio_url")
      return item.audio_url?.url?.trim().length > 0;
    return false;
  });
}

// Validate a single node for save-time (type-specific checks)
export function validateNodeForSave(node) {
  const config = node.data?.config;

  switch (node.type) {
    case NODE_TYPES.LLM_PROMPT: {
      if (!config || Object.keys(config).length === 0) {
        return {
          valid: false,
          node_id: node.id,
          message: "Node is not configured",
        };
      }
      const model = config.modelConfig?.model;
      if (!model || model.trim() === "") {
        return { valid: false, node_id: node.id, message: "Model is required" };
      }
      const messages = config.messages;
      if (!Array.isArray(messages) || messages.length === 0) {
        return {
          valid: false,
          node_id: node.id,
          message: "At least one message is required",
        };
      }
      const hasEmptyUserMessage = messages.some(
        (m) => m.role === "user" && !hasValidUserContent(m),
      );
      if (hasEmptyUserMessage) {
        return {
          valid: false,
          node_id: node.id,
          message: "User message content cannot be empty",
        };
      }
      return { valid: true, node_id: node.id, message: null };
    }

    case NODE_TYPES.AGENT: {
      const refVersionId = node.data?.ref_graph_version_id;
      if (!refVersionId || refVersionId.trim() === "") {
        return {
          valid: false,
          node_id: node.id,
          message: "Agent selection is required",
        };
      }
      return { valid: true, node_id: node.id, message: null };
    }

    default:
      // eval and other types: skip validation for now
      return { valid: true, node_id: node.id, message: null };
  }
}

// Detect cycles in a directed graph using DFS three-color marking
export function detectCycles(nodes, edges) {
  const adjacency = {};
  nodes.forEach((n) => {
    adjacency[n.id] = [];
  });
  edges.forEach((e) => {
    if (adjacency[e.source]) adjacency[e.source].push(e.target);
  });

  const WHITE = 0,
    GRAY = 1,
    BLACK = 2;
  const color = {};
  nodes.forEach((n) => {
    color[n.id] = WHITE;
  });

  function dfs(id) {
    color[id] = GRAY;
    for (const neighbor of adjacency[id] || []) {
      if (color[neighbor] === GRAY) return true;
      if (color[neighbor] === WHITE && dfs(neighbor)) return true;
    }
    color[id] = BLACK;
    return false;
  }

  return nodes.some((n) => color[n.id] === WHITE && dfs(n.id));
}

// Full graph validation for save time
export function validateGraphForSave(nodes, edges) {
  if (detectCycles(nodes, edges)) {
    return {
      valid: false,
      hasCycle: true,
      errors: [
        {
          nodeId: null,
          message:
            "Graph contains a cycle. Remove the circular connection before saving.",
        },
      ],
      invalidNodeIds: [],
    };
  }

  const results = nodes.map(validateNodeForSave);
  const invalid = results.filter((r) => !r.valid);

  if (invalid.length > 0) {
    return {
      valid: false,
      hasCycle: false,
      errors: invalid.map((r) => ({ nodeId: r.node_id, message: r.message })),
      invalidNodeIds: invalid.map((r) => r.node_id),
    };
  }

  return { valid: true, hasCycle: false, errors: [], invalidNodeIds: [] };
}

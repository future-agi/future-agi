import { diffLines } from "diff";
import { VERSION_STATUS } from "./constants";

export const CHANGE_STATUS = {
  CREATED: "created",
  UPDATED: "updated",
  DELETED: "deleted",
  REROUTED: "rerouted",
  UNCHANGED: "unchanged",
};

export const CHANGE_STATUS_LABEL = {
  [CHANGE_STATUS.CREATED]: "Created",
  [CHANGE_STATUS.UPDATED]: "Updated",
  [CHANGE_STATUS.DELETED]: "Deleted",
  [CHANGE_STATUS.REROUTED]: "Rerouting",
  [CHANGE_STATUS.UNCHANGED]: "No changes",
};

function sortKeys(value) {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === "object") {
    return Object.keys(value)
      .sort()
      .reduce((acc, key) => {
        if (value[key] === undefined) return acc;
        acc[key] = sortKeys(value[key]);
        return acc;
      }, {});
  }
  return value;
}

function stableStringify(value) {
  return `${JSON.stringify(sortKeys(value), null, 2)}\n`;
}

function splitDiffLines(text) {
  if (!text) return [];
  const lines = text.split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

function nodeName(node) {
  return node?.name || node?.id || "Untitled node";
}

function sourceId(connection) {
  return connection?.source_node_id || connection?.sourceNodeId;
}

function targetId(connection) {
  return connection?.target_node_id || connection?.targetNodeId;
}

/**
 * Strip identity/layout so a remapped draft can be compared with the last save.
 */
export function canonicalNodeBody(node = {}) {
  const body = {
    name: nodeName(node),
    type: node.type || null,
  };
  const templateId = node.node_template_id ?? node.nodeTemplateId;
  if (templateId != null) body.node_template_id = templateId;
  const prompt = node.prompt_template || node.promptTemplate;
  if (prompt) body.prompt_template = prompt;
  if (Array.isArray(node.ports) && node.ports.length > 0) {
    body.ports = node.ports;
  }
  const refVersion = node.ref_graph_version_id ?? node.refGraphVersionId;
  if (refVersion) body.ref_graph_version_id = refVersion;
  const mappings = node.input_mappings || node.inputMappings;
  if (Array.isArray(mappings) && mappings.length > 0) {
    body.input_mappings = mappings;
  }
  if (node.config && Object.keys(node.config).length > 0) {
    body.config = node.config;
  }
  return body;
}

export function toGraphSnapshot(source = {}) {
  return {
    nodes: source.nodes || [],
    connections: source.node_connections || source.nodeConnections || [],
  };
}

function idToNameMap(nodes = []) {
  return new Map(nodes.map((node) => [node.id, nodeName(node)]));
}

function namedConnections(snapshot) {
  const names = idToNameMap(snapshot.nodes);
  return (snapshot.connections || []).map((connection) => {
    const fromId = sourceId(connection);
    const toId = targetId(connection);
    return {
      from: names.get(fromId) || fromId,
      to: names.get(toId) || toId,
    };
  });
}

function incidentConnectionSet(snapshot, name) {
  return new Set(
    namedConnections(snapshot)
      .filter(
        (connection) => connection.from === name || connection.to === name,
      )
      .map((connection) => `${connection.from}\0${connection.to}`),
  );
}

function setsEqual(left, right) {
  if (left.size !== right.size) return false;
  for (const value of left) {
    if (!right.has(value)) return false;
  }
  return true;
}

/**
 * Pair previous/current nodes by name. Draft creation remaps UUIDs, so id is not stable.
 * Duplicate names are paired in order of appearance.
 */
export function pairNodesByName(previousNodes = [], currentNodes = []) {
  const previousByName = new Map();
  previousNodes.forEach((node) => {
    const name = nodeName(node);
    if (!previousByName.has(name)) previousByName.set(name, []);
    previousByName.get(name).push(node);
  });

  const used = new Map();
  const pairs = [];
  const created = [];

  currentNodes.forEach((current) => {
    const name = nodeName(current);
    const index = used.get(name) || 0;
    const previousList = previousByName.get(name) || [];
    const previous = previousList[index];
    used.set(name, index + 1);
    if (previous) {
      pairs.push({ previous, current });
    } else {
      created.push(current);
    }
  });

  const deleted = [];
  previousByName.forEach((list, name) => {
    const start = used.get(name) || 0;
    for (let i = start; i < list.length; i += 1) {
      deleted.push(list[i]);
    }
  });

  return { pairs, created, deleted };
}

function describeUpdated(previousBody, currentBody) {
  const prevPrompt = JSON.stringify(
    sortKeys(previousBody.prompt_template || null),
  );
  const nextPrompt = JSON.stringify(
    sortKeys(currentBody.prompt_template || null),
  );
  if (prevPrompt !== nextPrompt) {
    const prevMessages = JSON.stringify(
      sortKeys(previousBody.prompt_template?.messages || []),
    );
    const nextMessages = JSON.stringify(
      sortKeys(currentBody.prompt_template?.messages || []),
    );
    if (prevMessages !== nextMessages) {
      return "Prompt instructions changed";
    }
    const prevModel = previousBody.prompt_template?.model;
    const nextModel = currentBody.prompt_template?.model;
    if (prevModel !== nextModel) {
      return "Model changed";
    }
    return "Model settings changed";
  }
  if (previousBody.type !== currentBody.type) {
    return "Node type changed";
  }
  if (
    JSON.stringify(sortKeys(previousBody.ports || [])) !==
    JSON.stringify(sortKeys(currentBody.ports || []))
  ) {
    return "Ports changed";
  }
  if (previousBody.ref_graph_version_id !== currentBody.ref_graph_version_id) {
    return "Referenced agent version changed";
  }
  return "Node configuration changed";
}

export function describeChange(status, previousBody, currentBody) {
  switch (status) {
    case CHANGE_STATUS.CREATED:
      return "Node added";
    case CHANGE_STATUS.DELETED:
      return "Node removed";
    case CHANGE_STATUS.REROUTED:
      return "Connections changed";
    case CHANGE_STATUS.UNCHANGED:
      return "No changes";
    case CHANGE_STATUS.UPDATED:
      return describeUpdated(previousBody, currentBody);
    default:
      return "Node configuration changed";
  }
}

export function countLineChanges(original, current) {
  const parts = diffLines(original || "", current || "");
  let added = 0;
  let removed = 0;
  parts.forEach((part) => {
    const lines = splitDiffLines(part.value);
    if (part.added) added += lines.length;
    else if (part.removed) removed += lines.length;
  });
  return { added, removed };
}

export function computeAlignedLineDiff(original, current) {
  const parts = diffLines(original || "", current || "");
  const left = [];
  const right = [];
  let leftLine = 0;
  let rightLine = 0;

  const pushSplit = (text, side) => {
    splitDiffLines(text).forEach((line) => {
      if (side === "both") {
        leftLine += 1;
        rightLine += 1;
        left.push({ text: line, type: "unchanged", lineNumber: leftLine });
        right.push({ text: line, type: "unchanged", lineNumber: rightLine });
      } else if (side === "left") {
        leftLine += 1;
        left.push({ text: line, type: "removed", lineNumber: leftLine });
        right.push({ text: "", type: "filler", lineNumber: null });
      } else {
        rightLine += 1;
        left.push({ text: "", type: "filler", lineNumber: null });
        right.push({ text: line, type: "added", lineNumber: rightLine });
      }
    });
  };

  for (let i = 0; i < parts.length; i += 1) {
    const part = parts[i];
    if (part.removed && parts[i + 1]?.added) {
      const removedLines = splitDiffLines(part.value);
      const addedLines = splitDiffLines(parts[i + 1].value);
      const max = Math.max(removedLines.length, addedLines.length);
      for (let j = 0; j < max; j += 1) {
        if (j < removedLines.length) {
          leftLine += 1;
          left.push({
            text: removedLines[j],
            type: "removed",
            lineNumber: leftLine,
          });
        } else {
          left.push({ text: "", type: "filler", lineNumber: null });
        }
        if (j < addedLines.length) {
          rightLine += 1;
          right.push({
            text: addedLines[j],
            type: "added",
            lineNumber: rightLine,
          });
        } else {
          right.push({ text: "", type: "filler", lineNumber: null });
        }
      }
      i += 1;
    } else if (part.added) {
      pushSplit(part.value, "right");
    } else if (part.removed) {
      pushSplit(part.value, "left");
    } else {
      pushSplit(part.value, "both");
    }
  }

  return { left, right };
}

export function buildDefinitionDocument(
  snapshot = { nodes: [], connections: [] },
) {
  const nodes = (snapshot.nodes || [])
    .map((node) => canonicalNodeBody(node))
    .sort((a, b) => a.name.localeCompare(b.name));
  const connections = namedConnections(snapshot)
    .map((connection) => ({ from: connection.from, to: connection.to }))
    .sort((a, b) => `${a.from}:${a.to}`.localeCompare(`${b.from}:${b.to}`));
  return { nodes, connections };
}

export function definitionToJson(document) {
  return stableStringify(document);
}

function nodeDocument(snapshot, node) {
  const name = nodeName(node);
  return {
    node: canonicalNodeBody(node),
    connections: namedConnections(snapshot)
      .filter(
        (connection) => connection.from === name || connection.to === name,
      )
      .sort((a, b) => `${a.from}:${a.to}`.localeCompare(`${b.from}:${b.to}`)),
  };
}

const STATUS_ORDER = {
  [CHANGE_STATUS.CREATED]: 0,
  [CHANGE_STATUS.UPDATED]: 1,
  [CHANGE_STATUS.REROUTED]: 2,
  [CHANGE_STATUS.DELETED]: 3,
  [CHANGE_STATUS.UNCHANGED]: 4,
};

/**
 * Latest published version that is not the current draft.
 * Active/inactive versions count as saved. Drafts do not.
 */
export function pickBaselineVersion(versions = [], currentAgent = null) {
  const list = Array.isArray(versions) ? versions : [];
  const currentId = currentAgent?.version_id;
  const isDraft = currentAgent?.is_draft ?? true;

  if (!isDraft && currentId) {
    return (
      list.find((version) => version.id === currentId) || { id: currentId }
    );
  }

  return (
    list.find(
      (version) =>
        version.id !== currentId && version.status !== VERSION_STATUS.DRAFT,
    ) || null
  );
}

export function flattenGraphVersions(versionsData) {
  return (versionsData?.pages ?? []).flatMap(
    (page) => page.data?.result?.versions ?? [],
  );
}

export function buildAgentDefinitionFileName(agentName) {
  const raw = (agentName || "untitled").trim() || "untitled";
  const slug = raw
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${slug || "untitled"}-agent.json`;
}

/**
 * Classify every node (including unchanged) and compute definition + line diffs.
 */
export function classifySaveChanges({
  previousSnapshot = { nodes: [], connections: [] },
  currentSnapshot = { nodes: [], connections: [] },
  occurredAt = {},
} = {}) {
  const previous = {
    nodes: previousSnapshot.nodes || [],
    connections: previousSnapshot.connections || [],
  };
  const current = {
    nodes: currentSnapshot.nodes || [],
    connections: currentSnapshot.connections || [],
  };

  const { pairs, created, deleted } = pairNodesByName(
    previous.nodes,
    current.nodes,
  );
  const entries = [];

  created.forEach((node) => {
    const body = canonicalNodeBody(node);
    entries.push({
      id: node.id,
      name: nodeName(node),
      type: node.type || null,
      status: CHANGE_STATUS.CREATED,
      description: describeChange(CHANGE_STATUS.CREATED),
      occurredAt: occurredAt.current || null,
      previousBody: null,
      currentBody: body,
    });
  });

  pairs.forEach(({ previous: prevNode, current: nextNode }) => {
    const previousBody = canonicalNodeBody(prevNode);
    const currentBody = canonicalNodeBody(nextNode);
    const bodyChanged =
      JSON.stringify(sortKeys(previousBody)) !==
      JSON.stringify(sortKeys(currentBody));
    const routesChanged = !setsEqual(
      incidentConnectionSet(previous, nodeName(prevNode)),
      incidentConnectionSet(current, nodeName(nextNode)),
    );

    let status = CHANGE_STATUS.UNCHANGED;
    if (bodyChanged) status = CHANGE_STATUS.UPDATED;
    else if (routesChanged) status = CHANGE_STATUS.REROUTED;

    entries.push({
      id: nextNode.id,
      name: nodeName(nextNode),
      type: nextNode.type || currentBody.type,
      status,
      description: describeChange(status, previousBody, currentBody),
      occurredAt:
        status === CHANGE_STATUS.UNCHANGED
          ? occurredAt.previous || null
          : occurredAt.current || occurredAt.previous || null,
      previousBody,
      currentBody,
    });
  });

  deleted.forEach((node) => {
    const body = canonicalNodeBody(node);
    entries.push({
      id: node.id,
      name: nodeName(node),
      type: node.type || null,
      status: CHANGE_STATUS.DELETED,
      description: describeChange(CHANGE_STATUS.DELETED),
      occurredAt: occurredAt.previous || null,
      previousBody: body,
      currentBody: null,
    });
  });

  entries.sort((a, b) => {
    const rank = STATUS_ORDER[a.status] - STATUS_ORDER[b.status];
    if (rank !== 0) return rank;
    return a.name.localeCompare(b.name);
  });

  const previousDoc = buildDefinitionDocument(previous);
  const currentDoc = buildDefinitionDocument(current);
  const previousJson = definitionToJson(previousDoc);
  const currentJson = definitionToJson(currentDoc);
  const totals = countLineChanges(previousJson, currentJson);
  const aligned = computeAlignedLineDiff(previousJson, currentJson);

  const perNode = entries.map((entry) => {
    const prevNode =
      previous.nodes.find((node) => nodeName(node) === entry.name) || null;
    const nextNode =
      current.nodes.find((node) => nodeName(node) === entry.name) || null;
    const prevJson = prevNode
      ? definitionToJson(nodeDocument(previous, prevNode))
      : "";
    const nextJson = nextNode
      ? definitionToJson(nodeDocument(current, nextNode))
      : "";
    const lines = countLineChanges(prevJson, nextJson);
    return {
      name: entry.name,
      status: entry.status,
      added: lines.added,
      removed: lines.removed,
    };
  });

  return {
    entries,
    previousDocument: previousDoc,
    currentDocument: currentDoc,
    previousJson,
    currentJson,
    totals,
    aligned,
    perNode,
    hasBaseline: previous.nodes.length > 0 || previous.connections.length > 0,
  };
}

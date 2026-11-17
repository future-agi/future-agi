// W7 A2 (W7-3): the decision-tree editor's pure helpers — a starting tree,
// adding / changing / removing nodes, pointing an answer at a node (or at a
// new question, check or score), and the read-only graph's layout by depth.
// Validation, checks and the outline live in common/EvalTypes/dagModel.js.
import {
  DAG_SCHEMA,
  edgesOf,
  isChoice,
  numbersOf,
} from "src/sections/common/EvalTypes/dagModel";
import { isDecisionModel } from "src/sections/common/DecisionJudges/decisionModels";

// Characters of an answer label the graph shows (a display choice).
const GRAPH_LABEL_CHARS = 18;

const ANSWERS = {
  judge: ["Yes", "No"],
  check: ["Pass", "Fail"],
  eval: ["Pass", "Fail"],
};

/** The next free id with a prefix (`n3`, `leaf2`). */
export function nextId(dag, prefix) {
  const taken = new Set((dag?.nodes ?? []).map((n) => n.id));
  let i = 1;
  while (taken.has(`${prefix}${i}`)) i += 1;
  return `${prefix}${i}`;
}

/** A new node of a kind (answers point nowhere until the author picks). */
export function newNode(dag, kind) {
  if (kind === "leaf")
    return { id: nextId(dag, "leaf"), kind: "leaf", label: "", score: "" };
  const base = { id: nextId(dag, "n"), kind };
  const answers = (ANSWERS[kind] ?? ANSWERS.judge).map((label) => ({
    label,
    next: "",
  }));
  if (kind === "judge") return { ...base, question: "", answers };
  if (kind === "check")
    return {
      ...base,
      check: { type: "contains", on: "output", value: "" },
      answers,
    };
  return { ...base, template_id: "", answers };
}

/** An empty tree: one question to start from. */
export function blankDag(outputType = "score") {
  const first = {
    id: "n1",
    kind: "judge",
    question: "",
    answers: [
      { label: "Yes", next: "" },
      { label: "No", next: "" },
    ],
  };
  return {
    schema: DAG_SCHEMA,
    root: "n1",
    output_type: outputType,
    nodes: [first],
  };
}

const mapNodes = (dag, fn) => ({ ...dag, nodes: dag.nodes.map(fn) });

/** Change one node's fields. Changing its kind gives it that kind's answers. */
export function updateNode(dag, id, patch) {
  return mapNodes(dag, (n) => {
    if (n.id !== id) return n;
    if (patch.kind && patch.kind !== n.kind) {
      const fresh = newNode(dag, patch.kind);
      return { ...fresh, id: n.id };
    }
    return { ...n, ...patch };
  });
}

/**
 * Turn a yes/no question into a choice (2 options) or back. A choice asks
 * an LLM judge about each option, so it drops a decision judge (decision
 * models judge the tree's yes/no questions); the question then asks the
 * tree's judge.
 */
export function setChoice(dag, id, choice) {
  return mapNodes(dag, (n) => {
    if (n.id !== id || n.kind !== "judge") return n;
    if (choice && !isChoice(n)) {
      const { answers, judge_model: jm, ...rest } = n;
      return {
        ...rest,
        ...(jm && !isDecisionModel(jm) ? { judge_model: jm } : {}),
        options: (answers ?? []).map((a, i) => ({
          label: `Option ${i + 1}`,
          next: a.next,
        })),
      };
    }
    if (!choice && isChoice(n)) {
      const { options, ...rest } = n;
      return {
        ...rest,
        answers: ["Yes", "No"].map((label, i) => ({
          label,
          next: options[i]?.next ?? "",
        })),
      };
    }
    return n;
  });
}

const edgeKey = (n) => (isChoice(n) ? "options" : "answers");

/** Change one answer (or option) of a node: its label or where it goes. */
export function updateEdge(dag, id, index, patch) {
  return mapNodes(dag, (n) => {
    if (n.id !== id) return n;
    const k = edgeKey(n);
    return {
      ...n,
      [k]: n[k].map((a, i) => (i === index ? { ...a, ...patch } : a)),
    };
  });
}

/** Add or remove a choice option. */
export function addOption(dag, id) {
  return mapNodes(dag, (n) =>
    n.id === id && isChoice(n)
      ? {
          ...n,
          options: [
            ...n.options,
            { label: `Option ${n.options.length + 1}`, next: "" },
          ],
        }
      : n,
  );
}
export function removeOption(dag, id, index) {
  return mapNodes(dag, (n) =>
    n.id === id && isChoice(n)
      ? { ...n, options: n.options.filter((_, i) => i !== index) }
      : n,
  );
}

/**
 * Point an answer at a target: an existing node id, or `new:judge`,
 * `new:check`, `new:eval`, `new:leaf` (a new node appended and linked).
 */
export function pointEdge(dag, id, index, target) {
  if (!String(target).startsWith("new:"))
    return updateEdge(dag, id, index, { next: target });
  const node = newNode(dag, target.slice(4));
  const withNode = { ...dag, nodes: [...dag.nodes, node] };
  return updateEdge(withNode, id, index, { next: node.id });
}

/** Remove a node; answers that pointed at it point nowhere. */
export function removeNode(dag, id) {
  const nodes = dag.nodes
    .filter((n) => n.id !== id)
    .map((n) => {
      const k = edgeKey(n);
      if (!n[k]) return n;
      return {
        ...n,
        [k]: n[k].map((a) => (a.next === id ? { ...a, next: "" } : a)),
      };
    });
  return {
    ...dag,
    nodes,
    root: dag.root === id ? nodes[0]?.id ?? "" : dag.root,
  };
}

/** A node as the graph and the Goes-to list name it. */
export function nodeLabel(dag, n, nums = numbersOf(dag)) {
  if (n.kind === "leaf")
    return `${n.label || "Score"} (${n.score === "" || n.score == null ? "?" : Number(n.score).toFixed(2)})`;
  const head =
    n.kind === "judge"
      ? n.question || "Question"
      : n.kind === "check"
        ? `Check · ${n.check?.type ?? ""}`
        : "Eval";
  return `${nums[n.id]}. ${head}`;
}

/**
 * The graph's nodes and edges, laid out by depth from the first question
 * (unreached nodes go one row below the deepest). `highlight` is a set of
 * node ids (the last Test's path) and `selected` the node being edited.
 */
export function graphOf(
  dag,
  { highlight = new Set(), selected = null, pathEdges = new Set() } = {},
) {
  const byId = new Map((dag?.nodes ?? []).map((n) => [n.id, n]));
  const depth = new Map();
  const queue = byId.has(dag?.root) ? [[dag.root, 0]] : [];
  while (queue.length) {
    const [id, d] = queue.shift();
    if (depth.has(id)) continue;
    depth.set(id, d);
    edgesOf(byId.get(id)).forEach((a) => {
      if (byId.has(a.next) && !depth.has(a.next)) queue.push([a.next, d + 1]);
    });
  }
  const maxDepth = Math.max(0, ...depth.values());
  (dag?.nodes ?? []).forEach((n) => {
    if (!depth.has(n.id)) depth.set(n.id, maxDepth + 1);
  });
  const rows = new Map();
  const nums = numbersOf(dag);
  const nodes = (dag?.nodes ?? []).map((n) => {
    const d = depth.get(n.id);
    const col = rows.get(d) ?? 0;
    rows.set(d, col + 1);
    return {
      id: n.id,
      position: { x: col * 230, y: d * 110 },
      data: { label: nodeLabel(dag, n, nums) },
      selected: n.id === selected,
      className: [
        highlight.has(n.id) ? "dag-on-path" : "",
        n.id === selected ? "dag-selected" : "",
        n.kind === "leaf" ? "dag-leaf" : "",
      ]
        .filter(Boolean)
        .join(" "),
    };
  });
  const edges = [];
  // A long choice label is cut on the graph (the card shows it whole), so
  // the labels of a node's options do not run into each other.
  const edgeLabelOf = (label = "") =>
    label.length > GRAPH_LABEL_CHARS
      ? `${label.slice(0, GRAPH_LABEL_CHARS - 1)}…`
      : label;
  (dag?.nodes ?? []).forEach((n) =>
    edgesOf(n).forEach((a, i) => {
      if (!byId.has(a.next)) return;
      const key = `${n.id}:${a.label}`;
      edges.push({
        id: `${n.id}-${i}-${a.next}`,
        source: n.id,
        target: a.next,
        label: edgeLabelOf(a.label),
        animated: pathEdges.has(key),
      });
    }),
  );
  return { nodes, edges };
}

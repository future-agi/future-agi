/**
 * Jinja2-aware variable extraction using nunjucks' built-in parser.
 *
 * Nunjucks is a JS port of Jinja2 — its AST accurately identifies which
 * variables are external inputs vs loop-scoped, set-scoped, or built-in.
 * This mirrors the backend's jinja2.meta.find_undeclared_variables().
 */
import nunjucks from "nunjucks";

/**
 * Extract top-level input variable names from a Jinja2 template string.
 *
 * @param {string|null|undefined} template - The Jinja2 template text
 * @returns {string[]} Sorted, deduplicated list of input variable names
 */
export function extractJinjaVariables(template) {
  if (!template) return [];

  let ast;
  try {
    ast = nunjucks.parser.parse(template);
  } catch {
    return [];
  }

  const declared = new Set();
  const used = new Set();
  _walk(ast, declared, used);
  return [...used].sort();
}

/**
 * Recursively walk the nunjucks AST collecting declared (local) vs used (input) names.
 */
function _walk(node, declared, used) {
  if (!node) return;

  switch (node.typename) {
    case "For":
      // Collect loop target variables (e.g. "item" in "for item in items")
      if (node.name) _collectTargets(node.name, declared);
      // Walk the iterable expression (e.g. "items")
      _walk(node.arr, declared, used);
      // "loop" is Jinja2's implicit loop variable (loop.index, loop.first, etc.)
      declared.add("loop");
      _walk(node.body, declared, used);
      return;
    case "Set":
      // {% set x = expr %} — x is local, expr may reference inputs
      if (node.targets)
        node.targets.forEach((t) => _collectTargets(t, declared));
      if (node.value) _walk(node.value, declared, used);
      _walk(node.body, declared, used);
      return;
    case "Symbol":
      // A variable reference — if not declared locally, it's an input
      if (!declared.has(node.value)) used.add(node.value);
      return;
    case "LookupVal":
      // obj.field or obj["key"] — only the root (obj) matters
      _walk(node.target, declared, used);
      return;
    case "Filter":
      // {{ expr | filtername }} — walk the expression, skip the filter name
      _walk(node.args, declared, used);
      return;
    case "FunCall":
      _walk(node.name, declared, used);
      _walk(node.args, declared, used);
      return;
    default:
      break;
  }

  // Generic child walk
  (node.fields || []).forEach((k) => {
    const child = node[k];
    if (Array.isArray(child)) child.forEach((c) => _walk(c, declared, used));
    else if (child && typeof child === "object" && child.typename)
      _walk(child, declared, used);
  });
}

/**
 * Collect variable names from a for-loop or assignment target node.
 */
function _collectTargets(node, set) {
  if (!node) return;
  if (node.typename === "Symbol") {
    set.add(node.value);
    return;
  }
  (node.fields || []).forEach((k) => {
    const child = node[k];
    if (Array.isArray(child)) child.forEach((x) => _collectTargets(x, set));
    else if (child && child.typename) _collectTargets(child, set);
  });
}

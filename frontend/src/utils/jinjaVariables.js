/**
 * Jinja2-aware variable extraction using nunjucks' built-in parser.
 *
 * Nunjucks is a JS port of Jinja2 — its AST accurately identifies which
 * variables are external inputs vs loop-scoped, set-scoped, or built-in.
 * This mirrors the backend's jinja2.meta.find_undeclared_variables().
 */
import nunjucks from "nunjucks";

// Names in a default jinja2.Environment's globals. find_undeclared_variables()
// never reports these, so they are not inputs.
const JINJA_GLOBALS = new Set([
  "cycler",
  "dict",
  "joiner",
  "lipsum",
  "namespace",
  "range",
]);

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
    case "For": {
      // The iterable (e.g. "items") is read in the enclosing scope.
      _walk(node.arr, declared, used);
      // The loop target, "loop" and anything set in the body only exist
      // inside the loop, so the body gets its own copy of the scope.
      const loopScope = new Set(declared);
      if (node.name) _collectTargets(node.name, loopScope);
      loopScope.add("loop");
      _walk(node.body, loopScope, used);
      // {% else %} runs when the iterable is empty, outside the loop scope.
      _walk(node.else_, new Set(declared), used);
      return;
    }
    case "Set":
      if (node.value) {
        // {% set x = expr %}: expr is read before x is assigned, so
        // {% set x = x + 1 %} still needs x as an input.
        _walk(node.value, declared, used);
        node.targets.forEach((t) => _collectTargets(t, declared));
      } else {
        // {% set x %}...{% endset %}: x is local from the start and the
        // block body has its own scope.
        node.targets.forEach((t) => _collectTargets(t, declared));
        _walk(node.body, new Set(declared), used);
      }
      return;
    case "KeywordArgs":
      // f(key=value): "key" names a parameter, only the value can use inputs.
      node.children.forEach((pair) => _walk(pair.value, declared, used));
      return;
    case "Symbol":
      // A variable reference is an input unless it is local or a global.
      if (!declared.has(node.value) && !JINJA_GLOBALS.has(node.value))
        used.add(node.value);
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

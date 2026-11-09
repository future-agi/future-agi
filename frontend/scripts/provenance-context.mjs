// Recognize the local mark factory by return structure, not name/host/digest.
// Resolve lexical bindings so a shadowed factory or resource use cannot inherit
// the provenance classification. Every use of a constant must be metadata-only.
const keyOf = (n) => n?.name ?? n?.value;
export function provenanceContext(ast) {
  const parents = new Map(),
    scopes = new Map(),
    references = new Map();
  const factories = new Set(),
    identifiers = [];
  let readsSourceMetadata = false;
  const makeScope = (parent, fn = false) => ({
    parent,
    fn,
    bindings: new Map(),
  });
  const bind = (pattern, scope, declaration) => {
    if (!pattern) return;
    if (pattern.type === "Identifier")
      scope.bindings.set(pattern.name, declaration);
    else if (pattern.type === "AssignmentPattern")
      bind(pattern.left, scope, declaration);
    else if (pattern.type === "RestElement")
      bind(pattern.argument, scope, declaration);
    else if (pattern.type === "ArrayPattern")
      pattern.elements.forEach((p) => bind(p, scope, declaration));
    else if (pattern.type === "ObjectPattern")
      pattern.properties.forEach((p) =>
        bind(p.value || p.argument, scope, declaration),
      );
  };
  function walk(node, parent, scope) {
    if (!node || typeof node !== "object" || !node.type) return;
    parents.set(node, parent);
    // A source-property consumer invalidates the metadata-only argument rule.
    // Do not infer that a field remains inert merely because a factory names it.
    if (
      (node.type === "MemberExpression" && keyOf(node.property) === "source") ||
      (node.type === "ObjectPattern" &&
        node.properties.some((p) => keyOf(p.key) === "source"))
    )
      readsSourceMetadata = true;
    if (node.type === "FunctionDeclaration" || node.type === "ClassDeclaration")
      bind(node.id, scope, node);
    if (
      node.type === "Program" ||
      node.type === "BlockStatement" ||
      /Function/.test(node.type) ||
      node.type === "CatchClause"
    ) {
      scope = makeScope(
        scope,
        node.type === "Program" || /Function/.test(node.type),
      );
      if (/Function/.test(node.type)) {
        bind(node.id, scope, node);
        node.params?.forEach((p) => bind(p, scope, node));
      }
      if (node.type === "CatchClause") bind(node.param, scope, node);
    }
    scopes.set(node, scope);
    if (node.type === "VariableDeclarator") {
      let target = scope;
      if (parent.kind === "var")
        while (!target.fn && target.parent) target = target.parent;
      bind(node.id, target, node);
      const fn = node.init;
      if (
        fn?.type === "ArrowFunctionExpression" &&
        fn.body.type === "ObjectExpression"
      ) {
        const props = fn.body.properties,
          fields = ["mode", "source", "licence"];
        if (
          fields.every((field, i) =>
            props.some(
              (p) =>
                p.type === "Property" &&
                !p.computed &&
                keyOf(p.key) === field &&
                p.value.type === "Identifier" &&
                p.value.name === fn.params[i]?.name,
            ),
          ) &&
          props.every(
            (p) => p.type === "SpreadElement" || fields.includes(keyOf(p.key)),
          )
        )
          factories.add(node);
      }
    }
    if (node.type.startsWith("Import") && node.local)
      bind(node.local, scope, node);
    if (
      node.type === "Identifier" &&
      !(parent?.type === "VariableDeclarator" && parent.id === node) &&
      !(
        parent?.type === "Property" &&
        parent.key === node &&
        !parent.computed &&
        !parent.shorthand
      ) &&
      !(
        parent?.type === "MemberExpression" &&
        parent.property === node &&
        !parent.computed
      )
    )
      identifiers.push(node);
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach((v) => walk(v, node, scope));
      else if (value && typeof value === "object") walk(value, node, scope);
    }
  }
  walk(ast, null, makeScope(null, true));
  const resolve = (node) => {
    for (let scope = scopes.get(node); scope; scope = scope.parent)
      if (scope.bindings.has(node.name)) return scope.bindings.get(node.name);
    return null;
  };
  for (const node of identifiers) {
    const declaration = resolve(node);
    if (!declaration) continue;
    const list = references.get(declaration) || [];
    list.push(node);
    references.set(declaration, list);
  }
  for (const factory of factories) {
    if (
      !(references.get(factory) || []).every(
        (ref) =>
          parents.get(ref)?.type === "CallExpression" &&
          parents.get(ref).callee === ref,
      )
    )
      factories.delete(factory);
  }
  function isProvenance(node, seen = new Set()) {
    if (seen.has(node)) return false;
    const next = new Set(seen).add(node),
      parent = parents.get(node);
    if (!parent) return false;
    if (
      parent.type === "CallExpression" &&
      parent.callee.type === "Identifier" &&
      factories.has(resolve(parent.callee)) &&
      parent.arguments[1] === node
    )
      return true;
    if (
      parent.type === "TemplateLiteral" ||
      (parent.type === "BinaryExpression" && parent.operator === "+")
    )
      return isProvenance(parent, next);
    if (
      parent.type === "VariableDeclarator" &&
      parent.init === node &&
      parent.id.type === "Identifier"
    ) {
      const refs = references.get(parent) || [];
      return refs.length > 0 && refs.every((ref) => isProvenance(ref, next));
    }
    return false;
  }
  return readsSourceMetadata ? () => false : isProvenance;
}

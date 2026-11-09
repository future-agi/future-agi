const HTTP_METHODS = new Set([
  "get",
  "put",
  "post",
  "delete",
  "options",
  "head",
  "patch",
  "trace",
]);

function collectDefinitionRefs(obj, refs = new Set()) {
  if (!obj || typeof obj !== "object") return refs;
  if (Array.isArray(obj)) {
    obj.forEach((item) => collectDefinitionRefs(item, refs));
    return refs;
  }
  if (typeof obj.$ref === "string" && obj.$ref.startsWith("#/definitions/")) {
    refs.add(obj.$ref);
  }
  Object.values(obj).forEach((value) => collectDefinitionRefs(value, refs));
  return refs;
}

function resolveTransitiveDefinitions(allDefinitions, refs) {
  const allRefs = new Set(refs);
  let changed = true;
  while (changed) {
    changed = false;
    for (const ref of [...allRefs]) {
      const name = ref.replace("#/definitions/", "");
      const definition = allDefinitions[name];
      if (!definition) continue;
      const nestedRefs = collectDefinitionRefs(definition);
      for (const nestedRef of nestedRefs) {
        if (!allRefs.has(nestedRef)) {
          allRefs.add(nestedRef);
          changed = true;
        }
      }
    }
  }
  return allRefs;
}

export function buildManagementApiSwagger(swagger) {
  const paths = {};
  const refs = new Set();

  Object.entries(swagger.paths || {}).forEach(([pathName, pathSpec]) => {
    const filteredSpec = {};
    Object.entries(pathSpec || {}).forEach(([method, operation]) => {
      if (method === "parameters" || HTTP_METHODS.has(method)) {
        filteredSpec[method] = operation;
        collectDefinitionRefs(operation, refs);
      }
    });
    paths[pathName] = filteredSpec;
  });

  // Explicitly retained compatibility definitions still produce client exports,
  // without making them the active body of any endpoint or retaining all orphans.
  for (const [name, definition] of Object.entries(swagger.definitions || {})) {
    if (definition["x-client-retained"] === true) {
      refs.add(`#/definitions/${name}`);
    }
  }
  const allRefs = resolveTransitiveDefinitions(swagger.definitions || {}, refs);
  const definitions = {};
  for (const ref of allRefs) {
    const name = ref.replace("#/definitions/", "");
    if (swagger.definitions?.[name])
      definitions[name] = swagger.definitions[name];
  }

  return {
    ...swagger,
    info: {
      ...(swagger.info || {}),
      title: `${swagger.info?.title || "Future AGI API"} - management contracts`,
    },
    paths,
    definitions,
  };
}

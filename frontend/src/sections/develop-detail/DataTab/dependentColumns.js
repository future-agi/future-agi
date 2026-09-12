const ORIGIN_TO_OPERATION_TYPE = {
  api_call: "api_call",
  classification: "classify",
  conditional: "conditional",
  extracted_entities: "extract_entities",
  extracted_json: "extract_json",
  python_code: "execute_code",
  vector_db: "vector_db",
};

const RUNNING_COLUMN_STATUSES = new Set([
  "Editing",
  "ExperimentEvaluation",
  "NotStarted",
  "PartialRun",
  "Queued",
  "Running",
]);

const FAILED_COLUMN_STATUSES = new Set(["Cancelled", "Error", "Failed"]);

const getColumnId = (column) =>
  String(column?.field ?? column?.id ?? column?.col?.id ?? "");

const getColumnName = (column) =>
  column?.headerName ?? column?.name ?? column?.col?.name ?? "";

const getColumnOriginType = (column) =>
  column?.originType ??
  column?.origin_type ??
  column?.col?.originType ??
  column?.col?.origin_type;

const getColumnMetadata = (column) =>
  column?.metadata ?? column?.col?.metadata ?? {};

const getColumnStatus = (column) => column?.status ?? column?.col?.status ?? "";

const addExactReference = (value, knownColumnIds, references) => {
  if (typeof value !== "string") return;

  const reference = value.trim();
  const columnId = knownColumnIds.find(
    (candidateId) =>
      reference === candidateId ||
      reference.startsWith(`${candidateId}.`) ||
      reference.startsWith(`${candidateId}[`),
  );
  if (columnId) {
    references.add(columnId);
  }
};

const addTemplateReferences = (value, knownColumnIds, references) => {
  if (typeof value !== "string") return;

  Array.from(value.matchAll(/\{\{\s*([^}]+?)\s*\}\}/g)).forEach((match) =>
    addExactReference(match[1], knownColumnIds, references),
  );
};

const addTemplateReferencesDeep = (value, knownColumnIds, references) => {
  if (typeof value === "string") {
    addTemplateReferences(value, knownColumnIds, references);
    return;
  }

  if (Array.isArray(value)) {
    value.forEach((item) =>
      addTemplateReferencesDeep(item, knownColumnIds, references),
    );
    return;
  }

  if (value && typeof value === "object") {
    Object.values(value).forEach((item) =>
      addTemplateReferencesDeep(item, knownColumnIds, references),
    );
  }
};

const addApiBodyReferences = (body, knownColumnIds, references) => {
  if (typeof body === "string") {
    addTemplateReferences(body, knownColumnIds, references);
    return;
  }

  if (body && typeof body === "object" && !Array.isArray(body)) {
    Object.values(body).forEach((value) =>
      addTemplateReferences(value, knownColumnIds, references),
    );
  }
};

const addApiCallReferences = (metadata, knownColumnIds, references) => {
  const config = metadata?.config ?? metadata ?? {};

  addTemplateReferences(config.url, knownColumnIds, references);
  addApiBodyReferences(config.body, knownColumnIds, references);

  Object.values(config.params ?? {}).forEach((parameter) => {
    if (parameter?.type === "Variable") {
      addExactReference(parameter.value, knownColumnIds, references);
      addTemplateReferences(parameter.value, knownColumnIds, references);
    }
  });

  Object.values(config.headers ?? {}).forEach((header) => {
    if (header?.type === "Variable") {
      addExactReference(header.value, knownColumnIds, references);
      addTemplateReferences(header.value, knownColumnIds, references);
    }
  });
};

const addPythonCodeReferences = (code, columnsByName, references) => {
  if (typeof code !== "string") return;

  const referencedNames = new Set();
  const accessPatterns = [
    /kwargs\s*\.\s*get\s*\(\s*(["'])(.*?)\1/g,
    /kwargs\s*\[\s*(["'])(.*?)\1\s*\]/g,
  ];

  accessPatterns.forEach((pattern) => {
    Array.from(code.matchAll(pattern)).forEach((match) =>
      referencedNames.add(match[2]),
    );
  });

  referencedNames.forEach((name) => {
    (columnsByName.get(name) ?? []).forEach((columnId) =>
      references.add(columnId),
    );
  });
};

const addConditionalReferences = (
  metadata,
  knownColumnIds,
  columnsByName,
  references,
) => {
  (metadata?.config ?? []).forEach((branch) => {
    addTemplateReferences(branch?.condition, knownColumnIds, references);

    const node = branch?.branch_node_config ?? {};
    const config = node?.config ?? {};

    switch (node?.type) {
      case "classification":
      case "extract_entities":
      case "extract_json":
      case "column_value":
        addExactReference(config.column_id, knownColumnIds, references);
        break;
      case "retrieval":
      case "vector_db":
        addExactReference(config.column_id, knownColumnIds, references);
        addExactReference(config.query_key, knownColumnIds, references);
        break;
      case "extract_code":
      case "execute_code":
        addPythonCodeReferences(config.code, columnsByName, references);
        break;
      case "api_call":
        addApiCallReferences(config, knownColumnIds, references);
        break;
      case "run_prompt":
        addTemplateReferencesDeep(config, knownColumnIds, references);
        break;
      case "conditional":
        addConditionalReferences(
          config,
          knownColumnIds,
          columnsByName,
          references,
        );
        break;
      default:
        break;
    }
  });
};

export const getColumnDependencyIds = (column, allColumns) => {
  const knownColumnIds = allColumns.map(getColumnId).filter(Boolean);
  const columnsByName = new Map();

  allColumns.forEach((candidate) => {
    const name = getColumnName(candidate);
    const id = getColumnId(candidate);
    if (!name || !id) return;
    columnsByName.set(name, [...(columnsByName.get(name) ?? []), id]);
  });

  const references = new Set();
  const metadata = getColumnMetadata(column);

  switch (getColumnOriginType(column)) {
    case "classification":
    case "extracted_entities":
    case "extracted_json":
      addExactReference(metadata.column_id, knownColumnIds, references);
      break;
    case "vector_db":
      addExactReference(metadata.column_id, knownColumnIds, references);
      addExactReference(metadata.query_key, knownColumnIds, references);
      break;
    case "api_call":
      addApiCallReferences(metadata, knownColumnIds, references);
      break;
    case "python_code":
      addPythonCodeReferences(metadata.code, columnsByName, references);
      break;
    case "conditional":
      addConditionalReferences(
        metadata,
        knownColumnIds,
        columnsByName,
        references,
      );
      break;
    default:
      break;
  }

  return [...references];
};

export const findDependentColumns = (sourceColumnId, allColumns) => {
  const normalizedSourceId = String(sourceColumnId);
  const queuedSourceIds = [normalizedSourceId];
  const visitedColumnIds = new Set([normalizedSourceId]);
  const dependentsById = new Map();

  while (queuedSourceIds.length) {
    const currentSourceId = queuedSourceIds.shift();

    allColumns.forEach((column) => {
      const columnId = getColumnId(column);
      if (!columnId || visitedColumnIds.has(columnId)) return;

      const dependencyIds = getColumnDependencyIds(column, allColumns);
      if (!dependencyIds.includes(currentSourceId)) return;

      const operationType =
        ORIGIN_TO_OPERATION_TYPE[getColumnOriginType(column)];
      if (operationType) {
        visitedColumnIds.add(columnId);
        queuedSourceIds.push(columnId);
        dependentsById.set(columnId, {
          id: columnId,
          name: getColumnName(column),
          operationType,
          dependencyIds,
        });
      }
    });
  }

  const sortedDependents = [];
  const remainingDependents = [...dependentsById.values()];
  const completedIds = new Set([normalizedSourceId]);

  while (remainingDependents.length) {
    const nextIndex = remainingDependents.findIndex((column) =>
      column.dependencyIds
        .filter((dependencyId) => dependentsById.has(dependencyId))
        .every((dependencyId) => completedIds.has(dependencyId)),
    );

    const selectedIndex = nextIndex === -1 ? 0 : nextIndex;
    const [nextColumn] = remainingDependents.splice(selectedIndex, 1);
    sortedDependents.push(nextColumn);
    completedIds.add(nextColumn.id);
  }

  return sortedDependents;
};

export const getColumnDescriptor = (column, fallbackId = "") => ({
  id: getColumnId(column) || String(fallbackId),
  name: getColumnName(column),
});

export const toggleDependentColumnSelection = ({
  columnId,
  selectedIds,
  dependentColumns,
}) => {
  const selected = new Set(selectedIds);
  const dependentsById = new Map(
    dependentColumns.map((column) => [column.id, column]),
  );

  if (selected.has(columnId)) {
    selected.delete(columnId);

    let changed = true;
    while (changed) {
      changed = false;
      dependentColumns.forEach((column) => {
        if (
          selected.has(column.id) &&
          column.dependencyIds.some(
            (dependencyId) =>
              dependentsById.has(dependencyId) && !selected.has(dependencyId),
          )
        ) {
          selected.delete(column.id);
          changed = true;
        }
      });
    }
  } else {
    const selecting = new Set();
    const selectWithDependencies = (id) => {
      if (selected.has(id) || selecting.has(id)) return;
      selecting.add(id);
      const column = dependentsById.get(id);
      column?.dependencyIds.forEach((dependencyId) => {
        if (dependentsById.has(dependencyId)) {
          selectWithDependencies(dependencyId);
        }
      });
      selecting.delete(id);
      selected.add(id);
    };

    selectWithDependencies(columnId);
  }

  return dependentColumns
    .map((column) => column.id)
    .filter((id) => selected.has(id));
};

export const waitForColumnCompletion = async ({
  columnId,
  fetchColumns,
  pollInterval = 1500,
  maxAttempts = 400,
  wait = (duration) =>
    new Promise((resolve) => {
      setTimeout(resolve, duration);
    }),
}) => {
  const poll = async (attempt) => {
    const columns = await fetchColumns();
    const column = columns.find(
      (candidate) => getColumnId(candidate) === String(columnId),
    );

    if (!column) {
      throw new Error(`Column ${columnId} is no longer available.`);
    }

    const status = getColumnStatus(column);
    if (status === "Completed") return column;

    if (FAILED_COLUMN_STATUSES.has(status)) {
      throw new Error(
        `${getColumnName(column) || "Column"} finished with status ${status}.`,
      );
    }

    if (!RUNNING_COLUMN_STATUSES.has(status)) {
      throw new Error(
        `${getColumnName(column) || "Column"} has unexpected status ${status || "unknown"}.`,
      );
    }

    if (attempt >= maxAttempts - 1) {
      throw new Error(`Timed out waiting for column ${columnId} to finish.`);
    }

    await wait(pollInterval);
    return poll(attempt + 1);
  };

  return poll(0);
};

export const rerunDependentColumnsInOrder = async ({
  sourceColumnId,
  dependentColumns,
  fetchColumns,
  rerunColumn,
  waitOptions,
}) => {
  await waitForColumnCompletion({
    columnId: sourceColumnId,
    fetchColumns,
    ...waitOptions,
  });

  await dependentColumns.reduce(
    (previous, column) =>
      previous.then(async () => {
        await rerunColumn(column);
        await waitForColumnCompletion({
          columnId: column.id,
          fetchColumns,
          ...waitOptions,
        });
      }),
    Promise.resolve(),
  );
};

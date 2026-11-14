// The server owns the catalog. Only legacy native Jev IDs are retained here so
// saved evaluators remain readable while the catalog is loading or unavailable.
export const NATIVE_JEV_LABELS = {
  "jev-1.13.0": "Jev 1.13 (pinned)",
  "jev-1.14.0": "Jev 1.14 (pinned)",
  "jev-latest": "Jev latest",
};

export const isNativeJevModel = (model) =>
  Object.prototype.hasOwnProperty.call(NATIVE_JEV_LABELS, model);

export const isJevDecisionModel = (model) =>
  isNativeJevModel(model) ||
  (typeof model === "string" &&
    (model.startsWith("decision/openrouter/typesafe/jev-") ||
      model.startsWith("decision/openrouter/~typesafe/jev-")));

export const isDecisionModel = (model) =>
  isNativeJevModel(model) ||
  (typeof model === "string" && model.startsWith("decision/"));

export const DECISION_OUTPUT_NAMES = {
  noul: "Pass/Fail",
  choice: "Choices",
  score: "Score",
};

export const decisionQuestionType = (outputType) =>
  ({
    pass_fail: "noul",
    "Pass/Fail": "noul",
    deterministic: "choice",
    choices: "choice",
    percentage: "score",
    score: "score",
  })[outputType];

export const findDecisionGroup = (groups, model) =>
  groups.find((group) =>
    group.connections?.some((connection) => connection.model_name === model),
  );

export const decisionModelLabel = (model, groups = []) => {
  if (isNativeJevModel(model)) return NATIVE_JEV_LABELS[model];
  return findDecisionGroup(groups, model)?.label || model || "Decision model";
};

export function filterDecisionGroups(groups, search = "") {
  const query = search.trim().toLowerCase();
  if (!query) return groups;
  return groups.filter((group) =>
    [
      group.model_name,
      group.label,
      group.author,
      group.version,
      ...group.connections.flatMap((connection) => [
        connection.model_name,
        connection.provider,
        connection.label,
        NATIVE_JEV_LABELS[connection.model_name],
      ]),
    ].some((value) => value?.toLowerCase().includes(query)),
  );
}

// A search that names a connection ("openrouter") rather than the model
// itself should offer that connection when the row is chosen.
function connectionNamedBySearch(group, search) {
  const query = search?.trim().toLowerCase();
  if (!query) return undefined;
  const namesModel = [
    group.model_name,
    group.label,
    group.author,
    group.version,
  ].some((value) => value?.toLowerCase().includes(query));
  if (namesModel) return undefined;
  return group.connections.find((connection) =>
    [connection.provider, connection.label].some((value) =>
      value?.toLowerCase().includes(query),
    ),
  );
}

export function chooseDecisionConnection(group, model, preferredId, search) {
  // An explicitly selected/saved route wins even if its key was removed.
  return (
    group.connections.find(
      (connection) => connection.model_name === preferredId,
    ) ||
    group.connections.find((connection) => connection.model_name === model) ||
    connectionNamedBySearch(group, search) ||
    group.connections.find((connection) => connection.is_available) ||
    group.connections[0]
  );
}

export const legacyDecisionGroups = ["jev-1.13.0", "jev-latest"].map(
  (model) => ({
    model_name: model,
    label: NATIVE_JEV_LABELS[model],
    author: "TypeSafe",
    version: model === "jev-latest" ? "latest" : "versioned",
    question_types: ["noul", "choice", "score"],
    min_choices: 1,
    max_choices: 255,
    notice: "Text and JSON inputs only.",
    connections: [
      { model_name: model, provider: "typesafe", label: "TypeSafe" },
    ],
  }),
);

export function decisionCompatibilityError(group, outputType, choiceCount) {
  if (!group) return "";
  if (group.unavailable_reason) return group.unavailable_reason;
  const questionType = decisionQuestionType(outputType);
  if (questionType && !group.question_types.includes(questionType)) {
    return `${group.label} does not support ${DECISION_OUTPUT_NAMES[questionType]}. Choose another model or output type.`;
  }
  if (
    questionType === "choice" &&
    choiceCount != null &&
    choiceCount > 0 &&
    (choiceCount < group.min_choices || choiceCount > group.max_choices)
  ) {
    return `${group.label} requires ${group.min_choices} to ${group.max_choices} choices.`;
  }
  return "";
}

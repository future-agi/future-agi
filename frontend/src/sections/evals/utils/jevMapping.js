export const JEV_RUBRIC_ERROR =
  "Jev rubric score requires 2 to 10 ordered levels";

// Only the active question's fields are sent. Keep the other editor values in
// local state so changing models never discards the user's work.
export function buildJevMapping(outputType, choiceScores, draft = {}) {
  const questionType = {
    pass_fail: "noul",
    deterministic: "choice",
    percentage: "score",
  }[outputType];
  const labels = Object.keys(choiceScores || {});
  return {
    revision: "jev-map-v1",
    question_type: questionType,
    pass:
      questionType === "noul"
        ? {
            criteria_true: draft.pass?.criteria_true ?? "",
            criteria_false: draft.pass?.criteria_false ?? "",
          }
        : null,
    choice:
      questionType === "choice"
        ? {
            labels,
            descriptions: Object.fromEntries(
              labels.map((label) => [
                label,
                draft.choice?.descriptions?.[label] ?? "",
              ]),
            ),
          }
        : null,
    score:
      questionType === "score" ? { levels: draft.score?.levels || [] } : null,
    include_messages: draft.include_messages === true,
  };
}

export const hasExtraJevMessages = (messages = []) =>
  messages.some((message) => message.role !== "system");

const hasEntries = (value) =>
  Array.isArray(value)
    ? value.length > 0
    : value && typeof value === "object"
      ? Object.values(value).some(Boolean)
      : Boolean(value);

const hasMedia = (value) => {
  if (typeof value === "string")
    return /^(image|images|audio|video|pdf|file|binary)$/i.test(value);
  return (
    value && typeof value === "object" && Object.values(value).some(hasMedia)
  );
};

export function getJevValidationErrors({
  mapping,
  config,
  multiChoice,
  inputDataTypes,
  messages,
  choiceScores,
}) {
  const features = [];
  if (multiChoice) features.push("Multi-choice");
  if (hasEntries(config.tools)) features.push("Tools");
  if (hasEntries(config.knowledge_bases) || config.knowledge_base_id)
    features.push("Knowledge bases");
  if (config.check_internet) features.push("Internet check");
  if (hasMedia(inputDataTypes) || hasMedia(config.input_data_types))
    features.push("Media inputs");
  if (config.agent_mode && config.agent_mode !== "quick")
    features.push(`Agent mode (${config.agent_mode})`);
  if (
    hasEntries(config.few_shot_examples) ||
    hasEntries(config.ground_truth_few_shot)
  )
    features.push("Few-shot examples");
  if (
    Object.entries(config.data_injection || {}).some(([key, value]) =>
      key === "variables_only" || key === "variablesOnly"
        ? value === false
        : Boolean(value),
    )
  )
    features.push("Automatic context");
  const errors = features.map(
    (feature) =>
      `${feature} is not supported with Jev models. Nothing was changed or removed from your template.`,
  );
  if (!mapping.question_type)
    errors.push(
      "This output type is not supported with Jev models. Nothing was changed or removed from your template.",
    );
  if (mapping.question_type === "score") {
    if (Object.keys(choiceScores || {}).length > 0)
      errors.push(
        "Jev numeric rubrics cannot use choice scores. Remove the choice scores explicitly or choose another model.",
      );
    const levels = mapping.score.levels;
    if (
      levels.length < 2 ||
      levels.length > 10 ||
      levels.some(
        (level) =>
          typeof level !== "string" || !level.trim() || level.length > 2000,
      ) ||
      new Set(
        levels.map((level) =>
          typeof level === "string" ? level.trim() : level,
        ),
      ).size !== levels.length
    )
      errors.push(JEV_RUBRIC_ERROR);
  }
  if (mapping.question_type === "choice") {
    const { labels } = mapping.choice;
    if (
      !labels.length ||
      labels.length > 255 ||
      labels.some((label) => !label.trim() || label.length > 200) ||
      new Set(labels.map((label) => label.trim().toLowerCase())).size !==
        labels.length
    )
      errors.push(
        "Jev single choice requires 1 to 255 unique, non-empty labels.",
      );
  }
  const descriptions = mapping.pass || mapping.choice?.descriptions || {};
  if (
    Object.values(descriptions).some(
      (value) =>
        value != null && (typeof value !== "string" || value.length > 2000),
    )
  )
    errors.push("Jev descriptions must be at most 2000 characters.");
  if (hasExtraJevMessages(messages) && !mapping.include_messages)
    errors.push(
      "Confirm inclusion of extra messages before saving or testing with Jev.",
    );
  return errors;
}

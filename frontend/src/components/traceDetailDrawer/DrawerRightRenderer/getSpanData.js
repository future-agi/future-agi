import { groupFlattenedMessageAttrs } from "./flattenedMessageAttrs";

/**
 * Get span attributes with backward compatibility.
 * Prefers span_attributes (raw API), falls back to spanAttributes and evalAttributes for older data.
 * Supports both camelCase and snake_case keys for API response compatibility.
 */
export function getSpanAttributes(obj) {
  return (
    obj?.span_attributes || obj?.spanAttributes || obj?.eval_attributes || {}
  );
}

/**
 * Helper to get observation type from span object.
 * Supports both camelCase and snake_case keys.
 */
export function getObservationType(obj) {
  return obj?.observation_type || obj?.observationType;
}

/**
 * Helper to get provider logo from span object.
 * Supports both camelCase and snake_case keys.
 */
export function getProviderLogo(obj) {
  return obj?.provider_logo || obj?.providerLogo;
}

/**
 * Helper to get span events from span object.
 * Supports both camelCase and snake_case keys.
 */
export function getSpanEvents(obj) {
  return obj?.span_events || obj?.spanEvents;
}

/**
 * Helper to get model from span object.
 * Supports both camelCase and snake_case keys.
 */
export function getModel(obj) {
  return obj?.model;
}

/**
 * Helper to get input from span object.
 * Supports both camelCase and snake_case keys.
 */
export function getInput(obj) {
  return obj?.input;
}

/**
 * Helper to get output from span object.
 * Supports both camelCase and snake_case keys.
 */
export function getOutput(obj) {
  return obj?.output;
}

/**
 * Helper to get prompt name from span object.
 * Supports both camelCase and snake_case keys.
 */
export function getPromptName(obj) {
  return obj?.prompt_name || obj?.promptName;
}

/**
 * Helper to get prompt template ID from span object.
 * Supports both camelCase and snake_case keys.
 */
export function getPromptTemplateId(obj) {
  return obj?.prompt_template_id || obj?.promptTemplateId;
}

export function getLlmData(flatResponse = {}, type = "input") {
  if (!flatResponse || typeof flatResponse !== "object") {
    return type === "input"
      ? { inputMessage: {}, input: null }
      : { outputMessage: {}, output: null };
  }

  const attributes = getSpanAttributes(flatResponse);
  const data = flatResponse[type] || null;

  const message = extractMessages(attributes, type);

  return type === "input"
    ? { inputMessage: message, input: data }
    : { outputMessage: message, output: data };
}

export const isJson = (v) => {
  try {
    JSON.parse(v);
    return true;
  } catch (e) {
    return false;
  }
};

function extractMessages(evalAttributes = {}, type = "input") {
  const messageObj = [];
  const tempMessages = {};

  groupFlattenedMessageAttrs(evalAttributes, type).forEach(
    ({ index, entries }) => {
      if (!tempMessages[index]) {
        tempMessages[index] = {};
      }

      entries.forEach(({ property, value }) => {
        if (property === "message.role" || property === "role") {
          tempMessages[index].role = value;
        }

        if (
          property.startsWith("message.content") ||
          property.startsWith("content")
        ) {
          let content = value;

          if (typeof content === "object" && content !== null) {
            content = JSON.stringify(content, null, 2);
          }

          // property already excludes the message index (no `.splice(1)` needed).
          const contentParts = property
            .replace("message.content.", "")
            .replace("message.contents.", "")
            .replace("content.", "")
            .replace("contents.", "")
            .split(".");

          const parsedIndex = contentParts?.[0];
          const isMultipleContent = !isNaN(parseInt(parsedIndex));

          const contentProperty = contentParts.slice(2).join(".");

          if (!tempMessages[index].content) {
            tempMessages[index].content = [];
          }

          if (isMultipleContent) {
            // Some SDKs emit BOTH a flat `message.content` summary string and the
            // structured `message.contents.N.*` parts for the same message — e.g.
            // when an oversized image is masked to a "[image: …]" placeholder. If a
            // flat string already occupies this slot, keep it: attaching structured
            // sub-properties onto a string throws ("Cannot create property
            // 'image.url' on string") and crashes the whole trace view.
            const slot = tempMessages[index].content[parsedIndex];
            if (typeof slot !== "string") {
              if (!slot) {
                tempMessages[index].content[parsedIndex] = {};
              }
              if (contentProperty.length) {
                tempMessages[index].content[parsedIndex][contentProperty] =
                  content;
              }
            }
          } else if (
            typeof tempMessages[index].content[0] !== "object" ||
            tempMessages[index].content[0] === null
          ) {
            // Don't clobber already-built structured content with a flat summary.
            tempMessages[index].content[0] = content;
          }
        }
      });
    },
  );

  Object.keys(tempMessages)
    .sort((a, b) => parseInt(a) - parseInt(b))
    .forEach((key) => {
      if (tempMessages[key].role && tempMessages[key].content) {
        messageObj.push({
          role: tempMessages[key].role,
          content: tempMessages[key].content,
        });
      }
    });

  return messageObj;
}

export const parseRetrieveDocs = (selectedNode) => {
  const retrieveDocs = {};
  const attributes = getSpanAttributes(selectedNode);

  const contentEntries = Object.entries(attributes).filter(([key]) =>
    key.endsWith(".document.content"),
  );

  contentEntries.forEach(([key, content], index) => {
    const match = key.match(/retrieval\.documents\.(\d+)\.document\.content/);
    if (!match) return;
    const docIndex = match[1];

    const id = attributes[`retrieval.documents.${docIndex}.document.id`];
    const score = attributes[`retrieval.documents.${docIndex}.document.score`];

    const hasScore = score !== undefined;

    retrieveDocs[`doc${index + 1}`] = {
      id,
      score,
      hasScore,
      value: content,
    };
  });

  return retrieveDocs;
};

export const parseRankerDocs = (selectedNode, type = "output") => {
  const rankerDocs = {};
  const attributes = getSpanAttributes(selectedNode);

  const docType = type === "input" ? "inputDocuments" : "outputDocuments";

  const contentEntries = Object.entries(attributes).filter(
    ([key]) =>
      key.startsWith(`reranker.${docType}.`) &&
      key.endsWith(".document.content"),
  );

  contentEntries.forEach(([key, content], index) => {
    const match = key.match(
      new RegExp(`reranker\\.${docType}\\.(\\d+)\\.document\\.content`),
    );
    if (!match) return;

    const docIndex = match[1];

    const id = attributes[`reranker.${docType}.${docIndex}.document.id`];
    const rawScore =
      attributes[`reranker.${docType}.${docIndex}.document.score`];

    const hasScore = rawScore !== undefined;

    const score = hasScore ? Number(rawScore.toFixed(2)) : undefined;

    rankerDocs[`doc${index + 1}`] = {
      id,
      score,
      hasScore,
      value: content,
    };
  });

  return rankerDocs;
};

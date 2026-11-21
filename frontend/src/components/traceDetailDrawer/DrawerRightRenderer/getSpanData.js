import { groupFlattenedMessageAttrs } from "./flattenedMessageAttrs";
import { normalizeMessageRole } from "src/utils/message-role";
import { serializeMessageValue } from "src/utils/message-text";

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

// One structured part of a message, by whole path segments:
// `(message.)content(s).<part>[.<field>]`. The `message_content` wrapper
// (`messageContent` in camelCased responses) is one whole segment, so a field
// is never cut out of the middle of another segment's name.
const CONTENT_PART_PATH = /^(?:message\.)?contents?\.(\d+)(?:\.(.+))?$/;
const CONTENT_PART_WRAPPER = /^(?:message_content|messageContent)(?:\.(.+))?$/;

/**
 * Where a content property goes: `{ part, field }` for a structured part, or
 * `null` for flat content. `field` is the rest of the path kept whole, so
 * `image.url` stays `image.url` and an unknown field stays visible; it is `""`
 * when the value is the whole part.
 */
function parseContentProperty(property) {
  const match = CONTENT_PART_PATH.exec(property);
  if (!match) return null;
  const rest = match[2] ?? "";
  const wrapper = CONTENT_PART_WRAPPER.exec(rest);
  return { part: Number(match[1]), field: wrapper ? wrapper[1] ?? "" : rest };
}

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
          // Normalize before the truthiness filter so 0/false/null roles do not
          // silently discard otherwise valid message content.
          tempMessages[index].role = normalizeMessageRole(value);
        }

        if (
          property.startsWith("message.content") ||
          property.startsWith("content")
        ) {
          let content = value;

          if (typeof content === "object" && content !== null) {
            content = serializeMessageValue(content, 2);
          }

          // property already excludes the message index (no `.splice(1)` needed).
          const target = parseContentProperty(property);

          // Slots by part number, ordered numerically once the message is read.
          if (!tempMessages[index].content) {
            tempMessages[index].content = new Map();
          }
          const slots = tempMessages[index].content;

          if (target) {
            // Some SDKs emit BOTH a flat `message.content` summary string and the
            // structured `message.contents.N.*` parts for the same message — e.g.
            // when an oversized image is masked to a "[image: …]" placeholder. If a
            // flat string already occupies this slot, keep it: attaching structured
            // sub-properties onto a string throws ("Cannot create property
            // 'image.url' on string") and crashes the whole trace view.
            const slot = slots.get(target.part);
            if (!target.field) {
              // The value is the whole part; never clobber one already built.
              if (slot == null) slots.set(target.part, content);
            } else if (slot == null || typeof slot === "object") {
              const part = slot ?? {};
              // An own data property, even for a field such as `__proto__`.
              Object.defineProperty(part, target.field, {
                value: content,
                enumerable: true,
                configurable: true,
                writable: true,
              });
              slots.set(target.part, part);
            }
          } else {
            const slot = slots.get(0);
            if (typeof slot !== "object" || slot === null) {
              // Don't clobber already-built structured content with a flat summary.
              slots.set(0, content);
            }
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
          content: [...tempMessages[key].content.entries()]
            .sort(([a], [b]) => a - b)
            .map(([, part]) => part),
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

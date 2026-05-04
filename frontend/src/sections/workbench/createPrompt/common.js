import { PromptRoles } from "src/utils/constants";
import { extractVariables } from "./Playground/common";

export const dataTypeMapping = {
  "Pass/Fail": "boolean",
  score: "float",
  choices: "array",
};

export const getVariables = (currentPrompts, variableData, templateFormat) => {
  const extractedVariables = Array.from(
    new Set(
      currentPrompts.reduce((acc, { content, role }) => {
        if (role === PromptRoles.ASSISTANT) {
          return acc;
        }
        return [...acc, ...extractVariables(content, templateFormat)];
      }, []),
    ),
  );

  const finalVariables = Object.entries(variableData).reduce(
    (acc, [key, value]) => {
      if (extractedVariables.includes(key)) {
        acc[key] = value;
      }
      return acc;
    },
    {},
  );

  return finalVariables;
};

export const changeVersion = (version, direction, amount = 1) => {
  // Extract number from version string (e.g. "v1" -> 1)
  const versionNum = parseInt(version.version.replace("v", ""));

  // Handle direction
  if (direction === "+1" || direction === "up") {
    return `v${versionNum + amount}`;
  }
  if (direction === "-1" || direction === "down") {
    return `v${Math.max(0, versionNum - amount)}`;
  }

  return version.version;
};

export function throttleWithElse(mainFunction, delay, elseFunction) {
  let timerFlag = null; // Variable to keep track of the timer

  // Returning a throttled version
  return (...args) => {
    if (timerFlag === null) {
      // If there is no timer currently running
      mainFunction(...args); // Execute the main function
      timerFlag = setTimeout(() => {
        // Set a timer to clear the timerFlag after the specified delay
        timerFlag = null; // Clear the timerFlag to allow the main function to be executed again
      }, delay);
    } else {
      elseFunction?.(...args);
    }
  };
}

export function checkContentIsEmpty(results) {
  if (!Array.isArray(results)) return true;

  // check if any content is empty
  const anyContentEmpty = results.some((item) => {
    const content = item?.output;
    return content === null || (Array.isArray(content) && content.length === 0);
  });

  // check if any isAnimating flags are true
  const anyNotAnimating = results.some((item) => item?.isAnimating === true);

  return anyContentEmpty || anyNotAnimating;
}

// UI holds camelCase; backend reads snake_case. Rename at save/load boundaries.
// `model_detail` is UI-side snake_case by convention (ModelContainer reads
// `modelConfig.model_detail` directly) so it's not in this map.
const CONFIG_KEY_MAP = {
  voiceId: "voice_id",
  responseFormat: "response_format",
  topP: "top_p",
  maxTokens: "max_tokens",
  presencePenalty: "presence_penalty",
  frequencyPenalty: "frequency_penalty",
};

export function normalizeConfigurationForSave(configuration) {
  if (!configuration) return configuration;
  const result = { ...configuration };
  for (const [camel, snake] of Object.entries(CONFIG_KEY_MAP)) {
    if (result[camel] !== undefined) {
      result[snake] = result[camel];
      delete result[camel];
    }
  }
  return result;
}

export function normalizeConfigurationForLoad(configuration) {
  if (!configuration) return configuration;
  const result = { ...configuration };
  for (const [camel, snake] of Object.entries(CONFIG_KEY_MAP)) {
    const value = result[snake] !== undefined ? result[snake] : result[camel];
    delete result[snake];
    delete result[camel];
    if (value !== undefined) result[camel] = value;
  }
  return result;
}

export function runPromptOverSocket({
  url,
  payload,
  onMessage,
  onError,
  onClose,
}) {
  const socket = new WebSocket(url);

  socket.onopen = () => {
    socket.send(JSON.stringify(payload));
  };

  socket.onmessage = (event) => {
    if (onMessage) onMessage(JSON.parse(event.data));
  };

  socket.onerror = (err) => {
    if (onError) onError(err);
  };

  socket.onclose = () => {
    if (onClose) onClose();
  };

  return socket;
}

const AUDIO_PREVIEW_MODELS = [
  "gpt-4o-audio-preview",
  "gpt-4o-audio-preview-2024-10-01",
];

function promptHasAudioContent(prompt) {
  for (const message of prompt || []) {
    for (const content of message?.content || []) {
      if (content?.type === "audio_url") return true;
    }
  }
  return false;
}

function isModelRequiringAudio(modelConfig) {
  return (
    AUDIO_PREVIEW_MODELS.includes(modelConfig?.model) ||
    modelConfig?.model_detail?.type === "stt"
  );
}

/**
 * Validates that audio preview / STT models have audio content in their prompts.
 * @param {Object} modelConfigs - Configuration object containing model settings indexed by variant
 * @param {Object} prompts - Prompts object indexed by variant
 * @param {number|null} index - Optional specific variant index to check. If null, checks all variants.
 * @returns {boolean} true if validation passes (audio models have audio content), false otherwise
 */
export function checkIfAudioModelHasAudioContent(
  modelConfigs,
  prompts,
  index = null,
) {
  // When index is provided: check only that model/variant. Otherwise: check all.
  const indices =
    index !== null
      ? [index] // Single index: e.g. current playground variant
      : Object.keys(modelConfigs || {}); // All indices: e.g. batch run across variants

  for (const idx of indices) {
    const modelConfig = modelConfigs?.[idx];
    if (!isModelRequiringAudio(modelConfig)) {
      if (index !== null) return true; // single check: non-audio model is valid
      continue;
    }
    if (!promptHasAudioContent(prompts?.[idx]?.prompts)) return false;
  }

  return true;
}

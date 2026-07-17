import { enqueueSnackbar } from "notistack";
import { PromptRoles, WS_CLOSE_CODES } from "src/utils/constants";
import { normalizeModelOption } from "src/components/custom-model-dropdown/common";
import { extractVariables } from "./Playground/common";

/**
 * Predicate: does this CloseEvent indicate a BE-initiated auth failure
 * (unauthenticated / permission denied / not found)?
 *
 * Every WS consumer that calls runPromptOverSocket should branch on this in
 * its `onClose` — if true, settle the pending Promise, clear any polling
 * timers, and surface the reason. If false, fall through to the caller's
 * normal disconnect handling.
 *
 * Kept as a shared helper so a) all four consumers stay in sync and b) the
 * value set is testable in one place (see ws-close-codes.test.js).
 */
export const isAuthFailCloseCode = (event) =>
  event?.code === WS_CLOSE_CODES.PERMISSION_DENIED ||
  event?.code === WS_CLOSE_CODES.NOT_FOUND ||
  event?.code === WS_CLOSE_CODES.UNAUTHENTICATED;

/**
 * Extract the human-visible reason from an auth-fail CloseEvent. Falls back
 * to a canonical FE string when the server didn't attach one (e.g. some
 * browsers strip long reasons per RFC 6455's 123-byte limit).
 */
export const authFailMessage = (event) => event?.reason || "Permission denied";

/**
 * Shared `onClose` body for every `runPromptOverSocket` consumer's
 * server-initiated auth-fail handling (4001/4003/4004).
 *
 * Every call site used to hand-roll the same few lines: check
 * `isAuthFailCloseCode`, run its own cleanup (spinner off / clear timers /
 * close socket), snackbar the reason, and reject the pending Promise —
 * duplicated across `WorkbenchProvider`'s run + compare blocks,
 * `GeneratePromptDrawer`, `ImprovePromptDrawer`, and the DataTab
 * `ImprovePrompt`. This centralizes that; only the cleanup side effects and
 * rejection shape differ per call site, so those are passed in.
 *
 * @param {object} opts
 * @param {CloseEvent} opts.event
 * @param {() => boolean} [opts.isSettled] - Skip if the Promise already
 *   settled. Omit for call sites with no separate settled guard.
 * @param {() => void} [opts.markSettled]
 * @param {() => void} [opts.cleanup] - Reset loading state, clear timers,
 *   close the socket, etc.
 * @param {(reason: string) => void} opts.reject
 * @param {(reason: string) => Error} [opts.buildRejection] - Defaults to
 *   `new Error(reason)`; override for call sites that reject with a
 *   richer shape (e.g. `{ version, error }`).
 * @returns {boolean} true if this was an auth-fail close (fully handled —
 *   caller should stop), false otherwise (caller runs its own disconnect
 *   logic, e.g. HTTP-polling fallback).
 */
export function handleAuthFailClose({
  event,
  isSettled,
  markSettled,
  cleanup,
  reject,
  buildRejection = (reason) => new Error(reason),
}) {
  if (!isAuthFailCloseCode(event)) return false;
  if (isSettled && isSettled()) return true;
  markSettled?.();
  cleanup?.();
  const reason = authFailMessage(event);
  enqueueSnackbar(reason, { variant: "error" });
  reject(buildRejection(reason));
  return true;
}

/**
 * Shared `onMessage` body for handling a top-level `{type:"error"}` WS
 * frame — the BE's structured error path (permission denied, workspace not
 * found, unhandled exception, etc). Duplicated across `GeneratePromptDrawer`,
 * `ImprovePromptDrawer`, and the DataTab `ImprovePrompt`: short-circuit
 * before the consumer's own message-type filter, guard against double-firing
 * via `settled`, reset loading state, snackbar, and reject.
 *
 * @param {object} opts
 * @param {object} opts.wsData - The parsed WS message.
 * @param {() => boolean} opts.isSettled
 * @param {() => void} opts.markSettled
 * @param {() => void} [opts.cleanup]
 * @param {(reason: string) => void} opts.reject
 * @param {string} opts.defaultMessage - Used for both the snackbar and the
 *   rejection when the BE frame didn't include a `message`.
 * @returns {boolean} true if `wsData` was a `type:"error"` frame (caller
 *   should return immediately from its `onMessage`), false otherwise.
 */
export function handleWsErrorFrame({
  wsData,
  isSettled,
  markSettled,
  cleanup,
  reject,
  defaultMessage,
}) {
  if (wsData?.type !== "error") return false;
  if (isSettled()) return true;
  markSettled();
  cleanup?.();
  const message = wsData?.message || defaultMessage;
  enqueueSnackbar(message, { variant: "error" });
  reject(new Error(message));
  return true;
}

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
  if (result.model_detail) {
    result.model_detail = normalizeModelOption(result.model_detail);
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

  socket.onclose = (event) => {
    if (onClose) onClose(event);
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

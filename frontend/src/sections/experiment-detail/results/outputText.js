// A run's stored output as display text. Prompt runs store a string; agent
// (graph) runs store their output ports, `{"<port>": "..."}` (for example
// `{"response": "..."}`), and other run kinds may store any JSON value. Every place that renders an output as text goes through this so
// an object never reaches React as a child (React error #31).

/**
 * @param {unknown} value a stored output (`result.output`,
 *   `trial_outputs[i]`, `trials_detail[i].output`)
 * @returns {string} the port's text for a one-port agent output
 *   `{<port>: string}`, the string itself, "" for null/undefined, and JSON
 *   for any other value (several ports, nested values; media, call and chat
 *   outputs keep their JSON detection).
 */
export function outputText(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "object" && !Array.isArray(value)) {
    const keys = Object.keys(value);
    if (keys.length === 1 && typeof value[keys[0]] === "string")
      return value[keys[0]];
  }
  try {
    const json = JSON.stringify(value);
    return json === undefined ? String(value) : json;
  } catch {
    return String(value);
  }
}

/** A stored error as text: `{type, message}` shows its message. */
export function errorText(error) {
  if (error && typeof error === "object" && typeof error.message === "string")
    return error.message;
  return outputText(error);
}

/** `outputText` that keeps null/undefined as null (diff sources, lens values). */
export function outputTextOrNull(value) {
  return value == null ? null : outputText(value);
}

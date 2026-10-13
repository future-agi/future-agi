export const MAX_AGENT_EXAMPLES = 16;
export const MAX_EXAMPLE_BYTES = 2000;

export function exampleFieldError(value, label) {
  if (typeof value !== "string" || !value.trim())
    return `Enter an example ${label}.`;
  if (new TextEncoder().encode(value).length > MAX_EXAMPLE_BYTES)
    return `The example ${label} exceeds 2,000 UTF-8 bytes.`;
  return "";
}

export function examplesError(examples) {
  if (!Array.isArray(examples))
    return "Read the complete examples before changing them.";
  if (examples.length > MAX_AGENT_EXAMPLES) return "Use at most 16 examples.";
  for (const example of examples) {
    if (
      !example ||
      typeof example !== "object" ||
      Array.isArray(example) ||
      Object.keys(example).some((key) => !["prompt", "response"].includes(key))
    ) {
      return "Read the complete examples before changing them.";
    }
    const error =
      exampleFieldError(example.prompt, "prompt") ||
      exampleFieldError(example.response, "response");
    if (error) return error;
  }
  return "";
}

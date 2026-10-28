export const RESPONSE_ONLY_CHECKS = new Set([
  "hallucination-detection",
  "data-leakage-prevention",
]);

export const REQUEST_ONLY_CHECKS = new Set([
  "prompt-injection",
  "injection-detector",
  "topic-restriction",
  "language-detection",
  "system-prompt-protection",
]);

export function requestOnlyGuardrail(name, config = {}) {
  return (
    REQUEST_ONLY_CHECKS.has(name) ||
    ((name === "tool-permissions" ||
      ["tool_permissions", "tool_permission"].includes(config.provider)) &&
      config.apply_to === "request")
  );
}

export function withoutGuardrailRetries(config = {}) {
  const next = { ...config };
  if (next.on_block !== undefined) next.on_block = "reject";
  delete next.reflect_max_attempts;
  delete next.reflect_debug;
  return next;
}

export function guardrailStage(name, check = {}) {
  const config = check?.config || {};
  if (config.stage) return config.stage;
  // Old UI rules carried a default "pre" that the gateway did not use.
  // Only new opt-in rules may infer a stage from those top-level fields.
  if (config.on_block === "reflect" && (check.stage || check.phase)) {
    return check.stage || check.phase;
  }
  return RESPONSE_ONLY_CHECKS.has(name) ? "post" : "pre";
}

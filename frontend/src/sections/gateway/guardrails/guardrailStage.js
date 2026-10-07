// Where an org guardrail runs relative to the LLM call. Mirrors the gateway:
// built-in guardrails always run at their own stage, while Future AGI evals
// and external providers can check the prompt, the model's response or both,
// so the gateway runs them at the stage saved on the rule.

export const GUARDRAIL_STAGES = ["pre", "post", "both"];

export const GUARDRAIL_STAGE_LABELS = {
  pre: "Before LLM",
  post: "After LLM",
  both: "Before and after LLM",
};

// Org-config rule names the gateway knows by another name.
const RULE_TO_REGISTRY = new Map([
  ["pii-detector", "pii-detection"],
  ["injection-detector", "prompt-injection"],
  ["secrets-detector", "secret-detection"],
]);

// The gateway resolves these names to its built-in guardrails whatever the
// rule's config says, and they ignore the saved stage.
const AFTER_LLM_BUILT_INS = new Set([
  "hallucination-detection",
  "data-leakage-prevention",
]);
const BEFORE_LLM_BUILT_INS = new Set([
  "pii-detection",
  "content-moderation",
  "keyword-blocklist",
  "input-validation",
  "prompt-injection",
  "secret-detection",
  "topic-restriction",
  "language-detection",
  "system-prompt-protection",
]);

const STAGE_CONFIGURABLE_PROVIDERS = new Set([
  "futureagi",
  "lakera",
  "azure_content_safety",
  "presidio",
  "llama_guard",
  "bedrock_guardrails",
  "hiddenlayer",
  "dynamoai",
  "enkrypt",
  "ibm_ai",
  "pangea",
  "zscaler",
  "crowdstrike",
  "aporia",
  "lasso",
  "grayswan",
]);

// The provider the control plane fills in when one of these rules is saved
// without one (_RULE_PROVIDER_DEFAULTS in agentcc/services/config_push.py).
const RULE_PROVIDERS = new Map(
  Object.entries({
    "futureagi-eval": "futureagi",
    "llama-guard": "llama_guard",
    "azure-content-safety": "azure_content_safety",
    "presidio-pii": "presidio",
    "lakera-guard": "lakera",
    "bedrock-guardrails": "bedrock_guardrails",
    "hiddenlayer-guard": "hiddenlayer",
    "aporia-guard": "aporia",
    "pangea-guard": "pangea",
    "dynamoai-guard": "dynamoai",
    "enkrypt-guard": "enkrypt",
    "ibm-ai-detector": "ibm_ai",
    "grayswan-guard": "grayswan",
    "lasso-guard": "lasso",
    "crowdstrike-aidr": "crowdstrike",
    "zscaler-guard": "zscaler",
  }),
);

function normalizeStage(value) {
  if (typeof value !== "string") return null;
  const stage = value.trim().toLowerCase();
  return GUARDRAIL_STAGES.includes(stage) ? stage : null;
}

/**
 * Whether a guardrail rule's stage can be changed, and the stage it runs at.
 * Only a saved `stage` counts (the control plane pushes no legacy `phase`).
 * Without a valid one the guardrail runs at its own stage, before the LLM.
 */
export function getGuardrailStage(guardrail) {
  const name = guardrail?.name;
  const registryName = RULE_TO_REGISTRY.get(name) ?? name;
  if (AFTER_LLM_BUILT_INS.has(registryName)) {
    return { configurable: false, stage: "post" };
  }

  const provider = guardrail?.config?.provider ?? RULE_PROVIDERS.get(name);
  if (
    BEFORE_LLM_BUILT_INS.has(registryName) ||
    !STAGE_CONFIGURABLE_PROVIDERS.has(provider)
  ) {
    return { configurable: false, stage: "pre" };
  }

  return {
    configurable: true,
    stage: normalizeStage(guardrail.stage) ?? "pre",
  };
}

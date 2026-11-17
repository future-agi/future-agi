// Partner checks an org can add to its gateway, part two: security vendors. The gateway builds
// each one from the rule's config: `provider` picks the implementation
// (agentcc-gateway internal/guardrails/{external,futureagi,toolperm,mcpsec}).
// All of them run on the request, before the model.
//
// `fields` are the check's own settings, stored under the rule's `config`.
// A `password` field is write-only: the console never shows a stored value.

export const PARTNER_CHECKS_B = [
  {
    name: "hiddenlayer-guard",
    label: "HiddenLayer",
    provider: "hiddenlayer",
    description:
      "AI model security — adversarial attack & prompt injection detection",
    fields: [
      { key: "api_key", label: "API Key", type: "password", required: true },
      {
        key: "endpoint",
        label: "Endpoint URL",
        type: "text",
        defaultValue: "https://api.hiddenlayer.ai/api/v2/submit/text",
      },
      {
        key: "model_id",
        label: "Model ID",
        type: "text",
        placeholder: "Optional model ID for targeted scanning",
      },
    ],
  },
  {
    name: "aporia-guard",
    label: "Aporia Guardrails",
    provider: "aporia",
    description: "Checks the request with your Aporia project's guardrails",
    fields: [
      { key: "api_key", label: "API Key", type: "password", required: true },
      {
        key: "endpoint",
        label: "Endpoint URL",
        type: "text",
        defaultValue: "https://gr-prd.aporia.com",
      },
      {
        key: "project_id",
        label: "Project ID",
        type: "text",
        required: true,
      },
      // No "Policies": the gateway never sent the selection, only the
      // request text to the project, and an org's check reads the request
      // only, so answer policies (hallucination, RAG validation) could not
      // apply.
    ],
  },
  {
    name: "pangea-guard",
    label: "Pangea AI Guard",
    provider: "pangea",
    description: "Prompt injection, PII redaction, malware scanning",
    fields: [
      {
        key: "token",
        label: "Pangea Token",
        type: "password",
        required: true,
      },
      {
        key: "domain",
        label: "Domain",
        type: "text",
        required: true,
        placeholder: "aws.us.pangea.cloud",
      },
      {
        key: "services",
        label: "Services",
        type: "multiselect",
        options: ["ai_guard", "redact", "malware", "domain_intel"],
        defaultValue: ["ai_guard"],
      },
    ],
  },
  {
    name: "dynamoai-guard",
    label: "DynamoAI Guard",
    provider: "dynamoai",
    description: "DynamoGuard policy-based content moderation",
    fields: [
      { key: "api_key", label: "API Key", type: "password", required: true },
      {
        key: "endpoint",
        label: "Endpoint URL",
        type: "text",
        defaultValue: "https://api.dynamofl.com/moderation/analyze",
      },
      {
        key: "policy_ids",
        label: "Policy IDs",
        type: "text",
        placeholder: "Comma-separated policy IDs",
        required: true,
      },
      {
        key: "model_id",
        label: "Model ID",
        type: "text",
        placeholder: "Optional DynamoGuard model ID",
      },
      // No text type: an org's check reads the request only, so the text it
      // sends is model input (MODEL_INPUT, the gateway's default).
    ],
  },
  {
    name: "enkrypt-guard",
    label: "Enkrypt AI",
    provider: "enkrypt",
    description:
      "Toxicity, PII, injection attack, bias & content policy detection",
    fields: [
      { key: "api_key", label: "API Key", type: "password", required: true },
      {
        key: "endpoint",
        label: "Endpoint URL",
        type: "text",
        defaultValue: "https://api.enkryptai.com/guardrails/detect",
      },
      {
        key: "checks",
        label: "Detectors",
        type: "multiselect",
        options: [
          "toxicity",
          "nsfw",
          "pii",
          "injection_attack",
          "bias",
          "keyword_detector",
          "policy_violation",
          "topic_detector",
          "sponge_attack",
        ],
        defaultValue: ["toxicity", "injection_attack"],
      },
    ],
  },
  {
    name: "ibm-ai-detector",
    label: "IBM Granite Guardian",
    provider: "ibm_ai",
    description: "IBM watsonx Granite Guardian — risk detection via chat model",
    fields: [
      {
        key: "api_key",
        label: "API Key (Bearer Token)",
        type: "password",
        required: true,
      },
      {
        key: "endpoint",
        label: "Endpoint URL",
        type: "text",
        defaultValue: "https://us-south.ml.cloud.ibm.com/ml/v1/text/chat",
      },
      { key: "project_id", label: "Project ID", type: "text", required: true },
      {
        key: "model_id",
        label: "Model ID",
        type: "text",
        defaultValue: "ibm/granite-guardian-3-8b",
      },
      {
        key: "criteria_id",
        label: "Risk Criteria",
        type: "select",
        options: [
          "harm",
          "jailbreak",
          "social_bias",
          "violence",
          "profanity",
          "sexual_content",
          "unethical_behavior",
          // No "groundedness": it judges a response against its context, and
          // an org's check reads the request only.
        ],
        defaultValue: "harm",
      },
      {
        key: "version",
        label: "API Version",
        type: "text",
        defaultValue: "2024-03-14",
      },
    ],
  },
  {
    name: "grayswan-guard",
    label: "GraySwan Cygnal",
    provider: "grayswan",
    description: "AI risk monitoring with custom policies & reasoning modes",
    fields: [
      { key: "api_key", label: "API Key", type: "password", required: true },
      {
        key: "endpoint",
        label: "Endpoint URL",
        type: "text",
        defaultValue: "https://api.grayswan.ai/cygnal/monitor",
      },
      {
        key: "policy_id",
        label: "Policy ID",
        type: "text",
        placeholder: "Optional custom policy ID",
      },
      {
        key: "reasoning_mode",
        label: "Reasoning Mode",
        type: "select",
        options: ["off", "hybrid", "thinking"],
        defaultValue: "off",
      },
    ],
  },
  {
    name: "lasso-guard",
    label: "Lasso Security",
    provider: "lasso",
    description: "Content moderation, jailbreak detection & PII masking",
    fields: [
      { key: "api_key", label: "API Key", type: "password", required: true },
      {
        key: "endpoint",
        label: "Endpoint URL",
        type: "text",
        defaultValue: "https://server.lasso.security/gateway/v3",
      },
      {
        key: "user_id",
        label: "User ID",
        type: "text",
        placeholder: "Optional user identifier",
      },
      {
        key: "conversation_id",
        label: "Conversation ID",
        type: "text",
        placeholder: "Optional conversation identifier",
      },
    ],
  },
  {
    name: "crowdstrike-aidr",
    label: "CrowdStrike AIDR",
    provider: "crowdstrike",
    description: "AI threat detection and response",
    fields: [
      {
        key: "client_id",
        label: "Client ID",
        type: "password",
        required: true,
      },
      {
        key: "client_secret",
        label: "Client Secret",
        type: "password",
        required: true,
      },
      {
        key: "base_url",
        label: "Base URL",
        type: "text",
        defaultValue: "https://api.crowdstrike.com",
      },
      {
        key: "severity_threshold",
        label: "Severity Threshold (1-5)",
        type: "number",
        min: 1,
        max: 5,
        defaultValue: 3,
      },
    ],
  },
  {
    name: "zscaler-guard",
    label: "Zscaler AI Guard",
    provider: "zscaler",
    description: "Zero Trust DLP & threat detection for LLM traffic",
    fields: [
      { key: "api_key", label: "API Key", type: "password", required: true },
      {
        key: "cloud",
        label: "Cloud",
        type: "select",
        options: [
          "zscaler.net",
          "zscalerone.net",
          "zscalertwo.net",
          "zscalerthree.net",
        ],
        defaultValue: "zscaler.net",
      },
      {
        key: "tenant_id",
        label: "Tenant ID",
        type: "text",
        required: true,
      },
      {
        key: "dlp_profiles",
        label: "DLP Profile IDs",
        type: "text",
        placeholder: "Comma-separated profile IDs",
      },
    ],
  },
];

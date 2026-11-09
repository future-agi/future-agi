// Partner checks an org can add to its gateway, part one: Future AGI, model-hosted safety and agent tools. The gateway builds
// each one from the rule's config: `provider` picks the implementation
// (agentcc-gateway internal/guardrails/{external,futureagi,toolperm,mcpsec}).
// All of them run on the request, before the model.
//
// `fields` are the check's own settings, stored under the rule's `config`.
// A `password` field is write-only: the console never shows a stored value.

export const PARTNER_CHECKS_A = [
  {
    name: "futureagi-eval",
    label: "Future AGI Eval",
    provider: "futureagi",
    description: "Future AGI evaluation models (toxicity, bias, injection)",
    hideConfidence: true,
    fields: [
      {
        key: "eval_ids",
        label: "Protect Templates",
        type: "async-multiselect",
        required: true,
        fetchKey: "protectTemplates",
      },
      { key: "api_key", label: "API Key", type: "password", required: true },
      {
        key: "secret_key",
        label: "Secret Key",
        type: "password",
        required: true,
      },
      {
        key: "base_url",
        label: "Base URL",
        type: "text",
        defaultValue: "https://api.futureagi.com",
      },
    ],
  },
  {
    name: "llama-guard",
    label: "Llama Guard",
    provider: "llama_guard",
    description: "Meta Llama Guard content safety classification",
    fields: [
      {
        key: "endpoint",
        label: "Endpoint URL",
        type: "text",
        required: true,
        placeholder: "https://api.together.xyz/v1/chat/completions",
      },
      { key: "api_key", label: "API Key", type: "password", required: true },
      {
        key: "model",
        label: "Model",
        type: "text",
        defaultValue: "meta-llama/Llama-Guard-3-8B",
      },
    ],
  },
  {
    name: "azure-content-safety",
    label: "Azure Content Safety",
    provider: "azure_content_safety",
    description: "Hate, violence, sexual, self-harm severity scoring",
    fields: [
      {
        key: "endpoint",
        label: "Endpoint URL",
        type: "text",
        required: true,
        placeholder: "https://<resource>.cognitiveservices.azure.com",
      },
      { key: "api_key", label: "API Key", type: "password", required: true },
      {
        key: "categories",
        label: "Categories",
        type: "multiselect",
        options: ["Hate", "Violence", "SelfHarm", "Sexual"],
        defaultValue: ["Hate", "Violence", "SelfHarm", "Sexual"],
      },
      {
        key: "severity_threshold",
        label: "Severity Threshold (0-6)",
        type: "number",
        min: 0,
        max: 6,
        defaultValue: 2,
      },
    ],
  },
  {
    name: "presidio-pii",
    label: "Presidio PII",
    provider: "presidio",
    hideConfidence: true,
    description: "AI-powered PII detection via Microsoft Presidio",
    fields: [
      {
        key: "endpoint",
        label: "Endpoint URL",
        type: "text",
        defaultValue: "http://localhost:5001",
        placeholder: "http://localhost:5001",
      },
      {
        key: "language",
        label: "Language",
        type: "select",
        options: ["en", "es", "fr", "de", "it", "pt", "nl", "ja", "zh"],
        defaultValue: "en",
      },
      {
        key: "score_threshold",
        label: "Score Threshold (0-1)",
        type: "number",
        min: 0,
        max: 1,
        step: 0.05,
        defaultValue: 0.5,
      },
    ],
  },
  {
    name: "lakera-guard",
    label: "Lakera Guard",
    provider: "lakera",
    description: "Prompt injection & jailbreak detection",
    fields: [
      { key: "api_key", label: "API Key", type: "password", required: true },
      {
        key: "endpoint",
        label: "Endpoint URL",
        type: "text",
        defaultValue: "https://api.lakera.ai/v2/guard",
      },
      {
        key: "categories",
        label: "Categories",
        type: "multiselect",
        options: [
          "prompt_injection",
          "jailbreak",
          "harmful_content",
          "unknown_links",
        ],
        defaultValue: ["prompt_injection", "jailbreak", "harmful_content"],
      },
    ],
  },
  {
    name: "bedrock-guardrails",
    label: "AWS Bedrock Guardrails",
    provider: "bedrock_guardrails",
    description: "AWS-managed guardrail policies",
    fields: [
      {
        key: "guardrail_id",
        label: "Guardrail ID",
        type: "text",
        required: true,
      },
      {
        key: "guardrail_version",
        label: "Version",
        type: "text",
        defaultValue: "DRAFT",
      },
      {
        key: "region",
        label: "Region",
        type: "select",
        options: [
          "us-east-1",
          "us-west-2",
          "eu-west-1",
          "eu-central-1",
          "ap-southeast-1",
          "ap-northeast-1",
        ],
        defaultValue: "us-east-1",
      },
      {
        key: "access_key",
        label: "Access Key",
        type: "password",
        required: true,
      },
      {
        key: "secret_key",
        label: "Secret Key",
        type: "password",
        required: true,
      },
    ],
  },
  {
    name: "tool-permissions",
    label: "Tool Permissions",
    provider: "tool_permission",
    description: "Control which tools/functions an LLM can call",
    // It scores 1.0 on a match and 0 otherwise (toolperm.go), so no match
    // threshold grades it (stored-policy.js fixedScoreNote).
    hideConfidence: true,
    fixedScore: true,
    scope:
      "Reads the tools a request offers the model, by name pattern, on every route that carries tools.",
    fields: [
      {
        key: "mode",
        label: "Mode",
        type: "select",
        // toolperm.Check rejects every other policy mode.
        closedOptions: true,
        options: ["allowlist", "denylist", "audit"],
        defaultValue: "denylist",
      },
      {
        key: "tools",
        label: "Tool Patterns",
        type: "text",
        required: true,
        placeholder: "Comma-separated tool patterns, e.g. file_*, db_write",
      },
      // No "Apply To": an org's check reads the request only, so it checks
      // the tools the request offers and never the model's tool calls.
    ],
  },
  {
    name: "mcp-security",
    label: "MCP Security",
    provider: "mcp_security",
    description:
      "Checks the tool definitions and tool calls a request carries.",
    // Enforces model-request policy when the node's guardrail pipeline is on.
    // Allowed Servers is stored only; this is not /mcp endpoint protection.
    hideConfidence: true,
    fixedScore: true,
    scope:
      "Reads the tools a model request offers and the tool calls in its messages. Tool calls made through this gateway's /mcp endpoint are not checked.",
    fields: [
      {
        key: "blocked_tools",
        label: "Blocked Tools",
        type: "text",
        placeholder: "Comma-separated tool names to block",
        helperText:
          "Matches a model request that offers or calls one of these tools, by exact name.",
      },
      {
        key: "validate_inputs",
        label: "Validate Inputs",
        type: "select",
        // Keep the existing boolean repair and native-boolean handling.
        closedOptions: true,
        options: ["true", "false"],
        defaultValue: "true",
        helperText:
          "Matches a request whose tool calls carry command, SQL or script injection in their arguments.",
      },
      // No "Validate Outputs": an org's check reads the request only, so it
      // never sees the model's tool calls.
      {
        key: "max_calls_per_request",
        label: "Max Calls Per Request",
        type: "number",
        min: 1,
        max: 100,
        defaultValue: 10,
        helperText:
          "Matches a request whose messages carry more tool calls than this. Unset, the gateway uses 10.",
      },
    ],
  },
];

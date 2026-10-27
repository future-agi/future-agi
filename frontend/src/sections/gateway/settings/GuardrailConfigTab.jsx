/* eslint-disable react/prop-types */
import React, { useState } from "react";
import {
  Stack,
  Card,
  CardContent,
  Typography,
  TextField,
  MenuItem,
  FormControlLabel,
  Switch,
  IconButton,
  Button,
  Divider,
} from "@mui/material";
import Iconify from "src/components/iconify";
import InheritedBadge from "../components/InheritedBadge";
import GuardrailCheckDialog from "./GuardrailCheckDialog";

const GUARDRAIL_CHECKS = [
  { name: "pii-detection", label: "PII Detection", icon: "mdi:shield-account" },
  {
    name: "content-moderation",
    label: "Content Moderation",
    icon: "mdi:shield-check",
  },
  {
    name: "prompt-injection",
    label: "Prompt Injection",
    icon: "mdi:shield-alert",
  },
  {
    name: "keyword-blocklist",
    label: "Keyword Blocklist",
    icon: "mdi:shield-off",
    description: "Block prompts containing specific keywords or phrases.",
    fields: [
      {
        key: "words",
        label: "Blocked Keywords",
        type: "multiline-list",
        required: true,
        placeholder: "Enter one keyword or phrase per line",
        helperText:
          "Add one keyword or phrase per line. Commas are also supported.",
      },
    ],
  },
  {
    name: "secret-detection",
    label: "Secret Detection",
    icon: "mdi:shield-key",
  },
  {
    name: "topic-restriction",
    label: "Topic Restriction",
    icon: "mdi:shield-lock",
  },
  {
    name: "language-detection",
    label: "Language Detection",
    icon: "mdi:translate",
  },
  {
    name: "system-prompt-protection",
    label: "System Prompt Protection",
    icon: "mdi:shield-star",
  },
  {
    name: "hallucination-detection",
    label: "Hallucination Detection",
    icon: "mdi:shield-search",
  },
  {
    name: "data-leakage-prevention",
    label: "Data Leakage Prevention",
    icon: "mdi:shield-remove",
  },
];

// Future AGI star logo as inline SVG icon
const FutureAGIIcon = ({ size = 20, enabled }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 47 47"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    style={{ opacity: enabled ? 1 : 0.4 }}
  >
    <path
      d="M46.9 25.42L43.5 27.38L40.09 29.35L36.69 31.31L33.28 33.28L31.31 36.69L29.35 40.09L27.38 43.5L25.42 46.9H21.48L23.45 43.5L25.42 40.09L27.38 36.69L29.35 33.28L30.79 30.79L33.28 29.35L36.69 27.38L40.09 25.42L36.69 23.45L33.28 21.48L29.87 19.52L28.29 18.61L27.38 17.03L25.42 13.62L23.45 10.21L25.42 6.81L27.38 10.21L29.35 13.62L30.79 16.11L33.28 17.55L36.69 19.52L40.09 21.48L43.5 23.45L46.9 25.42Z"
      fill="url(#fagi1)"
    />
    <path
      d="M40.09 25.42L36.69 27.38L33.28 29.35L30.79 30.79L29.35 33.28L27.38 36.69L25.42 40.09L23.45 43.5L21.48 46.9L19.52 43.5L17.55 40.09L15.59 36.69L13.62 33.28L10.21 31.31L6.81 29.35L3.4 27.38L0 25.42V21.48L3.4 23.45L6.81 25.42L10.21 27.38L13.62 29.35L16.11 30.79L17.55 33.28L19.52 36.69L21.48 40.09L23.45 36.69L25.42 33.28L27.38 29.87L28.29 28.29L29.87 27.38L33.28 25.42L36.69 23.45L40.09 25.42Z"
      fill="url(#fagi2)"
    />
    <path
      d="M10.21 19.52L6.81 21.48L10.21 23.45L13.62 25.42L17.03 27.38L18.61 28.29L19.52 29.87L21.48 33.28L23.45 36.69L21.48 40.09L19.52 36.69L17.55 33.28L16.11 30.79L13.62 29.35L10.21 27.38L6.81 25.42L3.4 23.45L0 21.48L3.4 19.52L6.81 17.55L10.21 15.59L13.62 13.62L15.59 10.21L17.55 6.81L19.52 3.4L21.48 0H25.42L23.45 3.4L21.48 6.81L19.52 10.21L17.55 13.62L16.11 16.11L15.26 16.61L13.62 17.55L10.21 19.52Z"
      fill="url(#fagi3)"
    />
    <path
      d="M46.9 21.48V25.42L43.5 23.45L40.09 21.48L36.69 19.52L33.28 17.55L30.79 16.11L29.35 13.62L27.38 10.21L25.42 6.81L23.45 10.21L21.48 13.62L19.52 17.03L18.61 18.61L17.03 19.52L13.62 21.48L10.21 23.45L6.81 21.48L10.21 19.52L13.62 17.55L15.26 16.61L16.11 16.11L17.55 13.62L19.52 10.21L21.48 6.81L23.45 3.4L25.42 0L27.38 3.4L29.35 6.81L31.31 10.21L33.28 13.62L36.69 15.59L40.09 17.55L43.5 19.52L46.9 21.48Z"
      fill="url(#fagi4)"
    />
    <defs>
      <linearGradient
        id="fagi1"
        x1="34.19"
        y1="6.81"
        x2="34.19"
        y2="46.9"
        gradientUnits="userSpaceOnUse"
      >
        <stop stopColor="#CF6BE8" />
        <stop offset="1" stopColor="#7857FC" />
      </linearGradient>
      <linearGradient
        id="fagi2"
        x1="20.04"
        y1="21.48"
        x2="20.04"
        y2="46.9"
        gradientUnits="userSpaceOnUse"
      >
        <stop stopColor="#8B44FF" />
        <stop offset="1" stopColor="#7A40D9" />
      </linearGradient>
      <linearGradient
        id="fagi3"
        x1="12.71"
        y1="0"
        x2="12.71"
        y2="40.09"
        gradientUnits="userSpaceOnUse"
      >
        <stop stopColor="#CF6BE8" />
        <stop offset="1" stopColor="#7857FC" />
      </linearGradient>
      <linearGradient
        id="fagi4"
        x1="26.86"
        y1="0"
        x2="26.86"
        y2="25.42"
        gradientUnits="userSpaceOnUse"
      >
        <stop stopColor="#8B44FF" />
        <stop offset="1" stopColor="#7A40D9" />
      </linearGradient>
    </defs>
  </svg>
);

const MODEL_GUARDRAIL_CHECKS = [
  {
    name: "futureagi-eval",
    label: "Future AGI Eval",
    icon: "__futureagi__",
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
    icon: "mdi:shield-sword-outline",
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
    icon: "mdi:microsoft-azure",
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
    icon: "mdi:shield-account-outline",
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
    icon: "mdi:shield-bug-outline",
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
    icon: "mdi:aws",
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
    name: "hiddenlayer-guard",
    label: "HiddenLayer",
    icon: "mdi:shield-lock-outline",
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
    icon: "mdi:shield-check-outline",
    provider: "aporia",
    description: "Hallucination, injection, PII & topic restriction guardrails",
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
      {
        key: "policies",
        label: "Policies",
        type: "multiselect",
        options: [
          "hallucination",
          "injection",
          "pii",
          "topic_restriction",
          "rag_validation",
        ],
        defaultValue: ["hallucination", "injection", "pii"],
      },
    ],
  },
  {
    name: "pangea-guard",
    label: "Pangea AI Guard",
    icon: "mdi:shield-crown-outline",
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
    icon: "mdi:shield-half-full",
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
      {
        key: "text_type",
        label: "Text Type",
        type: "select",
        options: ["MODEL_INPUT", "MODEL_RESPONSE"],
        defaultValue: "MODEL_INPUT",
      },
    ],
  },
  {
    name: "enkrypt-guard",
    label: "Enkrypt AI",
    icon: "mdi:shield-key-outline",
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
    icon: "mdi:shield-search",
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
          "groundedness",
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
    icon: "mdi:shield-alert-outline",
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
    icon: "mdi:shield-edit-outline",
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
    icon: "mdi:shield-airplane-outline",
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
    icon: "mdi:shield-link-variant-outline",
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
  {
    name: "tool-permissions",
    label: "Tool Permissions",
    icon: "mdi:tools",
    provider: "tool_permission",
    description: "Control which tools/functions an LLM can call",
    fields: [
      {
        key: "mode",
        label: "Mode",
        type: "select",
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
      {
        key: "apply_to",
        label: "Apply To",
        type: "select",
        options: ["request", "response", "both"],
        defaultValue: "both",
      },
    ],
  },
  {
    name: "mcp-security",
    label: "MCP Security",
    icon: "mdi:shield-link-variant",
    provider: "mcp_security",
    description:
      "Model Context Protocol safety — validate MCP tool inputs/outputs",
    fields: [
      {
        key: "allowed_servers",
        label: "Allowed Servers",
        type: "text",
        placeholder: "Comma-separated server names or URLs",
      },
      {
        key: "blocked_tools",
        label: "Blocked Tools",
        type: "text",
        placeholder: "Comma-separated tool names to block",
      },
      {
        key: "validate_inputs",
        label: "Validate Inputs",
        type: "select",
        options: ["true", "false"],
        defaultValue: "true",
      },
      {
        key: "validate_outputs",
        label: "Validate Outputs",
        type: "select",
        options: ["true", "false"],
        defaultValue: "true",
      },
      {
        key: "max_calls_per_request",
        label: "Max Calls Per Request",
        type: "number",
        min: 1,
        max: 100,
        defaultValue: 10,
      },
    ],
  },
];

function getProviderSummary(check, meta) {
  if (!check || !meta) return null;
  const parts = [];
  if (check.provider) parts.push(check.provider);
  if (check.categories?.length) parts.push(check.categories.join(", "));
  if (check.endpoint) {
    try {
      parts.push(new URL(check.endpoint).hostname);
    } catch {
      parts.push(check.endpoint);
    }
  }
  return parts.length > 0 ? parts.join(" \u2022 ") : null;
}

// Map between org-config rule names and catalog names (bidirectional)
const RULE_TO_CATALOG = {
  "pii-detector": "pii-detection",
  "secrets-detector": "secret-detection",
  "injection-detector": "prompt-injection",
  // These are the same in both:
  "content-moderation": "content-moderation",
  "keyword-blocklist": "keyword-blocklist",
  "topic-restriction": "topic-restriction",
  "language-detection": "language-detection",
  "system-prompt-protection": "system-prompt-protection",
  "hallucination-detection": "hallucination-detection",
  "data-leakage-prevention": "data-leakage-prevention",
};
const _CATALOG_TO_RULE = Object.fromEntries(
  Object.entries(RULE_TO_CATALOG).map(([k, v]) => [v, k]),
);

function checksArrayToObject(checks) {
  const normalized = {};
  if (!Array.isArray(checks)) return normalized;

  for (const check of checks) {
    if (!check || typeof check !== "object") continue;
    const originalName = check.name;
    if (!originalName) continue;
    const catalogName = RULE_TO_CATALOG[originalName] || originalName;
    normalized[catalogName] = {
      ...check,
      _originalName: originalName,
    };
  }

  return normalized;
}

/**
 * Convert org-config "rules" array to "checks" object map that the UI uses.
 * Handles both formats: { rules: [...] } and { checks: { ... } }.
 */
function normalizeGuardrails(raw) {
  if (!raw) return { checks: {} };
  // Already has checks as object map — use it directly
  if (
    raw.checks &&
    typeof raw.checks === "object" &&
    !Array.isArray(raw.checks) &&
    Object.keys(raw.checks).length > 0
  ) {
    return raw;
  }
  if (Array.isArray(raw.checks) && raw.checks.length > 0) {
    return { ...raw, checks: checksArrayToObject(raw.checks) };
  }
  // Convert rules array → checks object map
  const rules = raw.rules;
  if (!Array.isArray(rules) || rules.length === 0)
    return { ...raw, checks: raw.checks || {} };

  const checks = { ...(raw.checks || {}) };
  for (const rule of rules) {
    // Map org-config name to catalog name so toggles light up
    const catalogName = RULE_TO_CATALOG[rule.name] || rule.name;
    checks[catalogName] = {
      enabled: rule.enabled !== false,
      action: rule.action || "block",
      confidence_threshold: rule.threshold ?? 0.8,
      stage: rule.stage || rule.phase,
      mode: rule.mode,
      config: rule.config,
      _originalName: rule.name, // preserve for save
    };
  }
  return { ...raw, checks };
}

const GuardrailConfigTab = ({ guardrails, onChange }) => {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingCheck, setEditingCheck] = useState(null);
  const [editingMeta, setEditingMeta] = useState(null);

  const config = normalizeGuardrails(guardrails || {});
  const checks = config.checks || {};

  const handlePipelineChange = (field, value) => {
    onChange({ ...config, [field]: value });
  };

  const handleEdit = (checkName, meta) => {
    setEditingCheck(checkName);
    setEditingMeta(meta || null);
    setDialogOpen(true);
  };

  const handleSaveCheck = (checkName, data) => {
    const existing = checks[checkName] || {};

    onChange({
      ...config,
      checks: {
        ...checks,
        [checkName]: {
          ...existing,
          ...data,
          config: data.config ?? existing.config,
        },
      },
    });
  };

  const handleToggleCheck = (checkName, enabled, meta) => {
    const existing = checks[checkName] || {
      action: "block",
      confidence_threshold: 0.8,
    };
    // For model-based checks, preserve provider field on toggle
    const providerField = meta?.provider ? { provider: meta.provider } : {};
    onChange({
      ...config,
      checks: {
        ...checks,
        [checkName]: {
          ...existing,
          ...providerField,
          enabled,
        },
      },
    });
  };

  const handleResetCheck = (checkName) => {
    const updated = { ...checks };
    delete updated[checkName];
    onChange({ ...config, checks: updated });
  };

  const renderCheckCard = ({ name, label, icon }, meta) => {
    const check = checks[name];
    const isCustom = Boolean(check);
    const enabled = check?.enabled ?? false;
    const action = check?.action || "block";
    const threshold =
      check?.confidence_threshold ?? check?.confidenceThreshold ?? 0.8;
    const hasMeta = Boolean(meta);
    const summary = meta?.provider ? getProviderSummary(check, meta) : null;

    return (
      <Card key={name} variant="outlined">
        <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
          >
            <Stack direction="row" alignItems="center" spacing={1}>
              {icon === "__futureagi__" ? (
                <FutureAGIIcon size={20} enabled={enabled} />
              ) : (
                <Iconify
                  icon={icon}
                  width={20}
                  sx={{ color: enabled ? "primary.main" : "text.disabled" }}
                />
              )}
              <Stack>
                <Typography variant="subtitle2">{label}</Typography>
                {hasMeta && meta.description && (
                  <Typography variant="caption" color="text.secondary">
                    {meta.description}
                  </Typography>
                )}
              </Stack>
              <InheritedBadge variant={isCustom ? "custom" : "inherited"} />
            </Stack>
            <Stack direction="row" alignItems="center" spacing={0.5}>
              <Switch
                size="small"
                checked={enabled}
                onChange={(e) =>
                  handleToggleCheck(name, e.target.checked, meta)
                }
              />
              <IconButton size="small" onClick={() => handleEdit(name, meta)}>
                <Iconify icon="mdi:pencil-outline" width={18} />
              </IconButton>
              {isCustom && (
                <Button
                  size="small"
                  color="inherit"
                  onClick={() => handleResetCheck(name)}
                >
                  Reset
                </Button>
              )}
            </Stack>
          </Stack>
          {isCustom && (
            <Stack
              direction="row"
              spacing={2}
              sx={{ mt: 0.5, pl: 3.5 }}
              flexWrap="wrap"
            >
              <Typography variant="caption" color="text.secondary">
                Action: {action}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Threshold: {Number(threshold).toFixed(2)}
              </Typography>
              {summary && (
                <Typography variant="caption" color="text.secondary">
                  {summary}
                </Typography>
              )}
            </Stack>
          )}
        </CardContent>
      </Card>
    );
  };

  return (
    <Stack spacing={2}>
      {/* Pipeline-level settings */}
      <Card variant="outlined">
        <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
          <Typography variant="subtitle2" gutterBottom>
            Pipeline Settings
          </Typography>
          <Stack direction="row" spacing={2} alignItems="center">
            <TextField
              select
              size="small"
              label="Mode"
              value={config.pipeline_mode || config.pipelineMode || "parallel"}
              onChange={(e) =>
                handlePipelineChange("pipeline_mode", e.target.value)
              }
              sx={{ minWidth: 140 }}
            >
              <MenuItem value="parallel">Parallel</MenuItem>
              <MenuItem value="sequential">Sequential</MenuItem>
            </TextField>
            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={config.fail_open ?? config.failOpen ?? true}
                  onChange={(e) =>
                    handlePipelineChange("fail_open", e.target.checked)
                  }
                />
              }
              label={<Typography variant="body2">Fail Open</Typography>}
            />
            <TextField
              size="small"
              label="Timeout (ms)"
              type="number"
              value={config.timeout_ms ?? config.timeoutMs ?? 5000}
              onChange={(e) =>
                handlePipelineChange("timeout_ms", Number(e.target.value))
              }
              sx={{ width: 120 }}
            />
          </Stack>
        </CardContent>
      </Card>

      {/* AI-powered / model-based checks */}
      <Typography variant="subtitle1">AI-Powered Checks</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: -1 }}>
        External ML models and services for advanced content safety, PII
        detection, and prompt injection prevention.
      </Typography>
      {MODEL_GUARDRAIL_CHECKS.map((item) => renderCheckCard(item, item))}

      <Divider sx={{ my: 1 }} />

      {/* Rule-based checks */}
      <Typography variant="subtitle1">Rule-Based Checks</Typography>
      {GUARDRAIL_CHECKS.map((item) => renderCheckCard(item, item))}

      <GuardrailCheckDialog
        open={dialogOpen}
        onClose={() => {
          setDialogOpen(false);
          setEditingMeta(null);
        }}
        onSave={handleSaveCheck}
        checkName={editingCheck}
        initialData={editingCheck ? checks[editingCheck] : null}
        providerMeta={editingMeta}
      />
    </Stack>
  );
};

export default GuardrailConfigTab;

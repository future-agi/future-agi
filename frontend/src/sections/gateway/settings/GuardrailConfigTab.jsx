/* eslint-disable react/prop-types */
import Section from "src/components/section";
import React, { useState } from "react";
import { Stack, Card, Typography, Divider } from "@mui/material";
import Iconify from "src/components/iconify";
import InheritedBadge from "../components/InheritedBadge";
import Chip from "@mui/material/Chip";
import ConfirmDialog from "../components/ConfirmDialog";
import {
  AdminButton,
  AdminButton as Button,
  AdminSwitch as Switch,
  AdminTextField as TextField,
  AdminIconButton as IconButton,
} from "../components/GatewayWriteControls";
import { useConfirmRemoval } from "../hooks/useConfirmRemoval";
import { GUARDRAILS_OFF_REASON as GUARDRAILS_OFF_TEXT } from "../utils/guardrailPipeline";
import {
  STORED_ONLY_CHECKS,
  STORED_ONLY_PARTNER_HELP,
  STORED_ONLY_PARTNER_TITLE,
  storedOnlyOffMessage,
} from "../constants/storedOnlyChecks";
import {
  storedSettings,
  storedToolSettings,
  storedCheckThreshold,
  storedCheckOn,
  NEVER_TRIGGERS_WARNING,
} from "./storedCheckSummary";
import GuardrailCheckDialog from "./GuardrailCheckDialog";
import { isStoredChecksMap } from "../hooks/useConfigPatch";
import PipelinePolicyFields from "./PipelinePolicyFields";
import {
  NOT_OFFERED_CHECKS,
  needsSetup,
  partnerConfigured,
  scoresOneOnMatch,
} from "../guardrails/catalog";
import { BUILT_IN_CHECKS } from "../guardrails/catalog/builtin-checks";
import {
  listEntryAsCheck,
  ruleAsCheck,
} from "../guardrails/catalog/rule-model";
import { storedThreshold } from "../guardrails/catalog/form-model";

// The settings a built-in check cannot run without, as the Guardrails page
// asks for them (topics for Topic Restriction, scripts for Language
// Detection): with none set, the gateway's check passes every request.
const builtInSettings = (name) => {
  const entry = BUILT_IN_CHECKS.find((check) => check.name === name);
  return {
    description: entry.description,
    fields: entry.fields,
    ...(entry.requiresOneOf
      ? {
          requiresOneOf: entry.requiresOneOf,
          requiresOneOfReason: entry.requiresOneOfReason,
        }
      : {}),
  };
};

// The check catalogs are exported for checkActions.test.jsx, which pins them
// to api_contracts/gateway/guardrail-check-actions.json.
// eslint-disable-next-line react-refresh/only-export-components
export const GUARDRAIL_CHECKS = [
  {
    name: "pii-detection",
    label: "PII Detection",
    icon: "solar:shield-user-linear",
  },
  {
    name: "content-moderation",
    label: "Content Moderation",
    icon: "solar:shield-check-linear",
  },
  {
    name: "prompt-injection",
    label: "Prompt Injection",
    icon: "solar:shield-warning-linear",
  },
  {
    name: "keyword-blocklist",
    label: "Keyword Blocklist",
    icon: "solar:shield-cross-linear",
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
    icon: "solar:shield-keyhole-linear",
  },
  {
    name: "topic-restriction",
    label: "Topic Restriction",
    icon: "solar:shield-keyhole-linear",
    ...builtInSettings("topic-restriction"),
  },
  {
    name: "language-detection",
    label: "Script Detection",
    icon: "solar:translation-linear",
    ...builtInSettings("language-detection"),
  },
  {
    name: "system-prompt-protection",
    label: "System Prompt Protection",
    icon: "solar:shield-linear",
  },
  {
    name: "hallucination-detection",
    label: "Hallucination Detection",
    icon: "solar:shield-linear",
  },
  {
    name: "data-leakage-prevention",
    label: "Data Leakage Prevention",
    icon: "solar:shield-cross-linear",
  },
];

// Future AGI star logo as inline SVG icon
const FutureAgiIcon = ({ size = 20, enabled }) => (
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
        {/* eslint-disable-next-line no-restricted-syntax -- ds: brand (logo) */}
        <stop stopColor="#CF6BE8" />
        {/* eslint-disable-next-line no-restricted-syntax -- ds: brand (logo) */}
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
        {/* eslint-disable-next-line no-restricted-syntax -- ds: brand (logo) */}
        <stop stopColor="#8B44FF" />
        {/* eslint-disable-next-line no-restricted-syntax -- ds: brand (logo) */}
        <stop offset="1" stopColor="#7857FC" />
      </linearGradient>
      <linearGradient
        id="fagi3"
        x1="12.71"
        y1="0"
        x2="12.71"
        y2="40.09"
        gradientUnits="userSpaceOnUse"
      >
        {/* eslint-disable-next-line no-restricted-syntax -- ds: brand (logo) */}
        <stop stopColor="#CF6BE8" />
        {/* eslint-disable-next-line no-restricted-syntax -- ds: brand (logo) */}
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
        {/* eslint-disable-next-line no-restricted-syntax -- ds: brand (logo) */}
        <stop stopColor="#8B44FF" />
        {/* eslint-disable-next-line no-restricted-syntax -- ds: brand (logo) */}
        <stop offset="1" stopColor="#7857FC" />
      </linearGradient>
    </defs>
  </svg>
);

// eslint-disable-next-line react-refresh/only-export-components
export const MODEL_GUARDRAIL_CHECKS = [
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
      // No defaultValue: an empty Base URL means "this platform", which is
      // what gets the org's keys injected. A filled-in api.futureagi.com
      // would be saved and point self-hosted installs at the cloud.
      {
        key: "base_url",
        label: "Base URL",
        type: "text",
        placeholder: "https://api.futureagi.com",
        helperText:
          "Leave empty to use this platform. Set only to target another Future AGI environment.",
      },
    ],
  },
  {
    name: "llama-guard",
    label: "Llama Guard",
    icon: "solar:shield-linear",
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
    icon: "solar:shield-user-linear",
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
    icon: "solar:shield-linear",
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
    icon: "solar:shield-keyhole-linear",
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
    icon: "solar:shield-check-linear",
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
    icon: "solar:shield-linear",
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
    icon: "solar:shield-linear",
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
    icon: "solar:shield-keyhole-linear",
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
    icon: "solar:shield-linear",
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
    icon: "solar:shield-warning-linear",
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
    icon: "solar:shield-user-linear",
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
    icon: "solar:shield-linear",
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
    icon: "solar:shield-network-linear",
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
    icon: "solar:toolbox-linear",
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
      // No "Apply To": an org's check reads the request only, so it checks
      // the tools the request offers and never the model's tool calls.
    ],
  },
  {
    name: "mcp-security",
    label: "MCP Security",
    icon: "solar:shield-network-linear",
    provider: "mcp_security",
    description:
      "Checks the tool definitions and tool calls a request carries.",
    fields: [
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
      // No "Validate Outputs": an org's check reads the request only, so it
      // never sees the model's tool calls.
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
    // As the Guardrails page reads it (rule-model normalizeGuardrails), so a
    // whole-section save sends an untouched entry back exactly as stored
    // (useConfigPatch guardrailsForSave).
    normalized[catalogName] = listEntryAsCheck(check);
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
    // As the Guardrails page reads it (rule-model ruleAsCheck): the threshold
    // the config push sends, 0.8 when none is stored and 0 (none sent) for a
    // stored null, so an untouched rule is saved back exactly as stored.
    checks[catalogName] = ruleAsCheck(rule);
  }
  return { ...raw, checks };
}

// What the gateway runs a check with when it carries no action or no
// threshold, by how the saved section is stored (see GuardrailCheckDialog).
const RULE_DEFAULTS = { action: "block", threshold: 0.8 };
const CHECKS_MAP_DEFAULTS = { action: "log", threshold: 0 };

// `saved` is the section as stored, which decides that meaning: a draft of a
// section stored as rules or as a list becomes a checks map once edited.
const GuardrailConfigTab = ({
  guardrails,
  saved = undefined,
  onChange,
  nodePipeline = undefined,
}) => {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingCheck, setEditingCheck] = useState(null);
  const [editingMeta, setEditingMeta] = useState(null);
  const [pendingOff, setPendingOff] = useState(null);

  const config = normalizeGuardrails(guardrails || {});
  const checks = config.checks || {};
  // Stored-only checks remain visible regardless of credential completeness.
  const storedOnlyChecks = MODEL_GUARDRAIL_CHECKS.filter(
    (meta) => STORED_ONLY_CHECKS.has(meta.name) && checks[meta.name] != null,
  );
  const absent = isStoredChecksMap(saved ?? guardrails)
    ? CHECKS_MAP_DEFAULTS
    : RULE_DEFAULTS;
  // 850(a): the node flag changes enforcement, not stored edits. The parent
  // OrgConfigEditor shows GUARDRAILS_OFF_BANNER; write controls still gate roles.
  const guardrailsOff = nodePipeline?.enabled === false;
  const [requestRemoval, confirmation] = useConfirmRemoval();

  const handlePipelineChange = (field, value) => {
    onChange({ ...config, [field]: value });
  };

  const handleEdit = (checkName, meta) => {
    setEditingCheck(checkName);
    setEditingMeta(meta || null);
    setDialogOpen(true);
  };

  // The dialog gives the whole check: a saved check as stored apart from the
  // fields the admin changed, or a new one.
  const handleSaveCheck = (checkName, data) => {
    onChange({
      ...config,
      checks: {
        ...checks,
        [checkName]: data,
      },
    });
  };

  const handleToggleCheck = (checkName, enabled, meta) => {
    // A check that cannot run without its settings (no topics, no scripts,
    // no keywords) opens them instead of being turned on as it is.
    if (enabled && meta?.fields && needsSetup(meta, checks[checkName] || {})) {
      handleEdit(checkName, meta);
      return;
    }
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

  // Turns a stored-only check off in the draft and changes nothing else it is
  // saved with (handleToggleCheck would also give it a provider).
  const handleTurnOff = (checkName) => {
    const stored = checks[checkName];
    onChange({
      ...config,
      checks: {
        ...checks,
        [checkName]: {
          ...(stored && typeof stored === "object" ? stored : {}),
          enabled: false,
        },
      },
    });
  };

  // Whether the gateway runs a stored-only check once the draft is saved:
  // as the config push reads its saved entry (storedCheckOn), not from the
  // editor's normalized draft, which reads a rule saved with a null `enabled`
  // as on. A rule or a policy-sync list entry saved without `enabled` runs;
  // one saved with null, or a checks-map entry without it or with null, does
  // not. The draft counts only when it turns the check off, the one change
  // the editors make to one (keptAsStored in useConfigPatch).
  const isOn = (name, check) => {
    if (check === false || check?.enabled === false) return false;
    return storedCheckOn(saved ?? guardrails, name);
  };

  // A stored partner check this release does not offer: what it is saved
  // with, read-only, and the two changes the console allows, each confirmed:
  // turn it off (when on) and remove it, both in the draft.
  const renderStoredOnlyCard = (meta) => {
    const { name, label, icon } = meta;
    const check = checks[name];
    const stored = check && typeof check === "object" ? check : {};
    const on = isOn(name, check);
    const action = stored.action || absent.action;
    const threshold = storedCheckThreshold(saved ?? guardrails, name);
    const details = [
      `Action: ${action}`,
      ...(!meta.hideConfidence || Number(threshold) >= 1
        ? [`Threshold: ${Number(threshold).toFixed(2)}`]
        : []),
      ...(name === "tool-permissions"
        ? storedToolSettings(stored)
        : storedSettings(meta, stored)),
    ];

    return (
      <Section
        component={Card}
        elevation={0}
        key={name}
        variant="panel"
        data-stored-only-check={name}
      >
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Stack direction="row" alignItems="center" spacing={1}>
            <Iconify
              icon={icon}
              width={20}
              sx={{ color: on ? "primary.main" : "text.disabled" }}
            />
            <Stack>
              <Typography variant="subtitle2">{label}</Typography>
              {meta.description && (
                <Typography variant="caption" color="text.secondary">
                  {meta.description}
                </Typography>
              )}
            </Stack>
            <Chip label="Read-only" size="small" variant="outlined" />
          </Stack>
          <Stack direction="row" alignItems="center" spacing={0.5}>
            {guardrailsOff ? (
              <Chip
                label="Not enforced"
                size="small"
                variant="outlined"
                title={GUARDRAILS_OFF_TEXT}
              />
            ) : (
              <Chip
                label={on ? "On" : "Off"}
                size="small"
                variant="outlined"
                color={on ? "success" : "default"}
              />
            )}
            {on && (
              <AdminButton
                size="small"
                color="inherit"
                aria-label={`Turn off ${label}`}
                onClick={() => setPendingOff({ name, label })}
              >
                Turn off
              </AdminButton>
            )}
            <AdminButton
              size="small"
              color="inherit"
              aria-label={`Remove ${label}`}
              onClick={() =>
                requestRemoval(label, () => handleResetCheck(name))
              }
            >
              Remove
            </AdminButton>
          </Stack>
        </Stack>
        <Stack
          direction="row"
          spacing={2}
          sx={{ mt: 0.5, pl: 3.5 }}
          flexWrap="wrap"
        >
          {details.map((text) => (
            <Typography
              key={text}
              variant="caption"
              color="text.secondary"
              sx={{ overflowWrap: "anywhere" }}
            >
              {text}
            </Typography>
          ))}
        </Stack>
        {Number(threshold) >= 1 && (
          <Typography variant="caption" color="warning.main">
            {NEVER_TRIGGERS_WARNING}
          </Typography>
        )}
      </Section>
    );
  };

  const renderCheckCard = ({ name, label, icon }, meta) => {
    const check = checks[name];
    const isCustom = Boolean(check);
    const enabled = check?.enabled ?? false;
    const action = check?.action || absent.action;
    // What the gateway runs it at: a saved confidence_threshold, even null
    // (run as 0), wins over confidenceThreshold (form-model storedThreshold).
    const threshold = storedThreshold(check, absent.threshold);
    const hasMeta = Boolean(meta);
    const summary = meta?.provider ? getProviderSummary(check, meta) : null;

    return (
      <Section component={Card} elevation={0} key={name} variant="panel">
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Stack direction="row" alignItems="center" spacing={1}>
            {icon === "__futureagi__" ? (
              <FutureAgiIcon size={20} enabled={enabled} />
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
            {guardrailsOff && isCustom && (
              <Chip label="Not enforced" size="small" variant="outlined" />
            )}
            <Switch
              size="small"
              checked={enabled}
              inputProps={{ "aria-label": `Turn ${label} on or off` }}
              onChange={(e) => handleToggleCheck(name, e.target.checked, meta)}
            />
            <IconButton
              size="small"
              aria-label={`Configure ${label}`}
              onClick={() => handleEdit(name, meta)}
            >
              <Iconify icon="solar:pen-linear" width={20} />
            </IconButton>
            {isCustom && (
              <Button
                size="small"
                color="inherit"
                onClick={() =>
                  requestRemoval(label, () => handleResetCheck(name))
                }
              >
                Remove
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
            {!meta?.hideConfidence && !scoresOneOnMatch(name, check) && (
              <Typography variant="caption" color="text.secondary">
                Threshold: {Number(threshold).toFixed(2)}
              </Typography>
            )}
            {summary && (
              <Typography variant="caption" color="text.secondary">
                {summary}
              </Typography>
            )}
          </Stack>
        )}
      </Section>
    );
  };

  const partnerCards = MODEL_GUARDRAIL_CHECKS.filter(
    (item) =>
      !NOT_OFFERED_CHECKS.has(item.name) &&
      !STORED_ONLY_CHECKS.has(item.name) &&
      partnerConfigured(item, checks[item.name]),
  );

  return (
    <Stack spacing={2}>
      {confirmation}
      <ConfirmDialog
        open={Boolean(pendingOff)}
        title="Turn off check"
        message={
          pendingOff
            ? storedOnlyOffMessage(pendingOff.label, "Save & Activate")
            : ""
        }
        confirmLabel="Turn off"
        onClose={() => setPendingOff(null)}
        onConfirm={() => {
          if (pendingOff) handleTurnOff(pendingOff.name);
          setPendingOff(null);
        }}
      />
      {/* Pipeline-level settings */}
      <Section component={Card} elevation={0} variant="panel">
        <Typography variant="subtitle2" gutterBottom>
          Pipeline Settings
        </Typography>
        <PipelinePolicyFields nodePipeline={nodePipeline} />
        <Stack direction="row" spacing={2} alignItems="center">
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
      </Section>

      {/* Partner and Future AGI checks: each calls a service outside the
          gateway, and is listed only once the organization's config holds
          that service's account (partnerConfigured). */}
      {partnerCards.length > 0 && (
        <>
          <Typography variant="subtitle1">AI-Powered Checks</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: -1 }}>
            External ML models and services for advanced content safety, PII
            detection, and prompt injection prevention.
          </Typography>
          {partnerCards.map((item) => renderCheckCard(item, item))}
          <Divider sx={{ my: 1 }} />
        </>
      )}

      {storedOnlyChecks
        .filter((meta) => meta.name === "tool-permissions")
        .map((meta) => renderStoredOnlyCard(meta))}

      {/* Partner checks the org saved that this release does not offer:
          read-only, kept and pushed as stored. */}
      {storedOnlyChecks.some((meta) => meta.name !== "tool-permissions") && (
        <>
          <Typography variant="subtitle1">
            {STORED_ONLY_PARTNER_TITLE}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: -1 }}>
            {STORED_ONLY_PARTNER_HELP}
          </Typography>
          {storedOnlyChecks
            .filter((meta) => meta.name !== "tool-permissions")
            .map((meta) => renderStoredOnlyCard(meta))}

          <Divider sx={{ my: 1 }} />
        </>
      )}

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
        absentAction={absent.action}
        absentThreshold={absent.threshold}
      />
    </Stack>
  );
};

export default GuardrailConfigTab;

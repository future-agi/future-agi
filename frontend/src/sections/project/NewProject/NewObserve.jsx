import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  LinearProgress,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router-dom";
import { Events, handleOnDocsClicked, trackEvent } from "src/utils/Mixpanel";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import InstructionTitle from "./InstructionTitle";
import InstructionCodeCopy from "./InstructionCodeCopy";
import ObserveInstruments from "./ObserveInstuments";
import {
  CustomTab,
  CustomTabs,
  TabWrapper,
} from "src/sections/develop/AddDatasetDrawer/AddDatasetStyle";
import { persistObserveSetupIntent } from "src/sections/projects/observeOnboardingRoute";
import {
  appendSetupQuickStartAttributionToHref,
  readPersistedSetupQuickStartAttribution,
} from "src/sections/auth/jwt/setup-org-quick-starts";

const CODE_SECTION_ALIASES = {
  installationGuide: "installation_guide",
  projectAddCode: "project_add_code",
};

const ONBOARDING_SOURCE_VALUES = new Set(["onboarding", "onboarding_email"]);
const LANGUAGE_VALUES = new Set(["python", "typescript"]);

const normalizeSetupValue = (value) =>
  typeof value === "string"
    ? value.trim().toLowerCase().replaceAll("-", "_")
    : "";

const normalizeSetupLanguage = (value) => {
  const normalizedValue = normalizeSetupValue(value);
  return LANGUAGE_VALUES.has(normalizedValue) ? normalizedValue : "";
};

const normalizeInstrumentId = (value) => {
  const normalizedValue = normalizeSetupValue(value);
  if (["llama_index", "llamaindex"].includes(normalizedValue)) {
    return "llama_index";
  }
  if (normalizedValue === "openaiagents") return "openai_agents";
  return normalizedValue;
};

const hasQuickStartAttribution = (attribution = {}) =>
  Boolean(attribution.quickStartId || attribution.quick_start_id);

const onboardingSetupReturnHref = ({
  instrumentId,
  language,
  quickStartAttribution,
} = {}) => {
  const params = new URLSearchParams({
    setup: "true",
    source: "onboarding",
    credential_step: "done",
  });
  if (instrumentId) params.set("provider", instrumentId);
  if (instrumentId && LANGUAGE_VALUES.has(language)) {
    params.set("language", language);
  }
  const attribution = hasQuickStartAttribution(quickStartAttribution)
    ? quickStartAttribution
    : readPersistedSetupQuickStartAttribution();
  return appendSetupQuickStartAttributionToHref(
    `/dashboard/observe?${params.toString()}`,
    attribution,
  );
};

const apiKeysOnboardingHref = ({
  instrumentId,
  language,
  quickStartAttribution,
} = {}) => {
  const params = new URLSearchParams({
    source: "onboarding",
    target: "observe_first_trace",
    action: "create",
    key_name: "Observe first trace",
    return_to: onboardingSetupReturnHref({
      instrumentId,
      language,
      quickStartAttribution,
    }),
  });
  return `${paths.dashboard.settings.apiKeys}?${params.toString()}`;
};

const FIRST_TRACE_STEPS = [
  {
    id: "package",
    label: "Pick SDK package",
    description: "Choose the package that makes the model call in your app.",
  },
  {
    id: "setup",
    label: "Create keys",
    description: "Create Future AGI keys and keep your provider key loaded.",
  },
  {
    id: "run",
    label: "Run one request",
    description: "Paste the package code and run one request from that SDK.",
  },
  {
    id: "review",
    label: "Review and create eval",
    description: "We open the trace, then guide you to evaluator setup.",
  },
];

const SETUP_INSTRUMENT_PRIORITY = [
  "openai",
  "anthropic",
  "langchain",
  "openai_agents",
  "llama_index",
  "bedrock",
  "mcp",
];

const INSTRUMENT_INSTALL_COMMANDS = {
  python: {
    anthropic: "pip install traceAI-anthropic anthropic",
    bedrock: "pip install traceAI-bedrock boto3",
    langchain: "pip install traceAI-langchain langchain-openai",
    llama_index: "pip install traceAI-llamaindex llama-index",
    mcp: "pip install traceAI-mcp traceAI-openai-agents openai-agents",
    openai: "pip install traceAI-openai openai",
    openai_agents: "pip install traceAI-openai-agents openai-agents",
  },
  typescript: {
    anthropic:
      "npm install @traceai/fi-core @traceai/anthropic @opentelemetry/instrumentation @anthropic-ai/sdk",
    langchain:
      "npm install @traceai/fi-core @traceai/langchain @opentelemetry/instrumentation",
    mcp: "npm install @traceai/fi-core @traceai/mcp @opentelemetry/instrumentation",
    openai:
      "npm install @traceai/fi-core @traceai/openai @opentelemetry/instrumentation openai",
    openai_agents:
      "npm install @traceai/fi-core @traceai/openai-agents @opentelemetry/instrumentation",
  },
};

const PROVIDER_RUNTIME_KEYS = {
  anthropic: ["ANTHROPIC_API_KEY"],
  bedrock: [
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
    "BEDROCK_MODEL_ID",
  ],
  langchain: ["OPENAI_API_KEY"],
  llama_index: ["OPENAI_API_KEY"],
  mcp: ["OPENAI_API_KEY", "MCP_SERVER_URL", "MCP_SERVER_TOKEN"],
  openai: ["OPENAI_API_KEY"],
  openai_agents: ["OPENAI_API_KEY"],
};

const runtimeKeySetupCode = (instrumentId, language = "bash") => {
  const keys = PROVIDER_RUNTIME_KEYS[instrumentId] || [];
  if (!keys.length) return "";
  if (language === "python") {
    return keys
      .map((key) => `os.environ.setdefault("${key}", "...")`)
      .join("\n");
  }
  if (language === "typescript") {
    return keys.map((key) => `process.env.${key} = "...";`).join("\n");
  }
  return keys.map((key) => `export ${key}="..."`).join("\n");
};

const FALLBACK_INSTRUMENT_SNIPPETS = {
  anthropic: {
    name: "Anthropic",
    Python: {
      code: `from traceai_anthropic import AnthropicInstrumentor

AnthropicInstrumentor().instrument(tracer_provider=trace_provider)`,
      sample_request_code: `import os
import anthropic

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

message = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=256,
    messages=[{"role": "user", "content": "Say hello in one sentence."}],
)

print(message.content)`,
    },
    TypeScript: {
      code: `import { registerInstrumentations } from "@opentelemetry/instrumentation";
import { AnthropicInstrumentation } from "@traceai/anthropic";

const anthropicInstrumentation = new AnthropicInstrumentation({});

registerInstrumentations({
  instrumentations: [anthropicInstrumentation],
  tracerProvider,
});`,
      sample_request_code: `import Anthropic from "@anthropic-ai/sdk";

const anthropic = new Anthropic({
  apiKey: process.env.ANTHROPIC_API_KEY,
});

const message = await anthropic.messages.create({
  model: "claude-sonnet-4-20250514",
  max_tokens: 256,
  messages: [{ role: "user", content: "Say hello in one sentence." }],
});

console.log(message.content);`,
    },
  },
  bedrock: {
    name: "Bedrock",
    Python: {
      code: `from traceai_bedrock import BedrockInstrumentor

BedrockInstrumentor().instrument(tracer_provider=trace_provider)`,
      sample_request_code: `import json
import os
import boto3

client = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION"])

response = client.invoke_model(
    modelId=os.environ["BEDROCK_MODEL_ID"],
    body=json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 256,
        "messages": [{"role": "user", "content": "Say hello in one sentence."}],
    }),
)

print(response["body"].read().decode("utf-8"))`,
    },
  },
  langchain: {
    name: "LangChain",
    Python: {
      code: `from traceai_langchain import LangChainInstrumentor

LangChainInstrumentor().instrument(tracer_provider=trace_provider)`,
      sample_request_code: `from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4o-mini")
response = llm.invoke("Say hello in one sentence.")

print(response.content)`,
    },
  },
  llama_index: {
    name: "LlamaIndex",
    Python: {
      code: `from traceai_llamaindex import LlamaIndexInstrumentor

LlamaIndexInstrumentor().instrument(tracer_provider=trace_provider)`,
      sample_request_code: `from llama_index.core import Document, VectorStoreIndex

index = VectorStoreIndex.from_documents([
    Document(text="Future AGI helps teams observe, test, and improve AI systems.")
])
query_engine = index.as_query_engine()

print(query_engine.query("What does Future AGI help teams do?"))`,
    },
  },
  mcp: {
    name: "MCP",
    Python: {
      code: `from traceai_mcp import MCPInstrumentor
from traceai_openai_agents import OpenAIAgentsInstrumentor

MCPInstrumentor().instrument(tracer_provider=trace_provider)
OpenAIAgentsInstrumentor().instrument(tracer_provider=trace_provider)`,
      sample_request_code: `import os
from agents import Agent, Runner
from agents.mcp import MCPServerStreamableHttp

mcp_server = MCPServerStreamableHttp(
    params={
        "url": os.environ["MCP_SERVER_URL"],
        "headers": {"Authorization": f"Bearer {os.environ['MCP_SERVER_TOKEN']}"},
    },
)

agent = Agent(
    name="MCP trace test",
    instructions="Call a safe tool if one is available, then summarize the result.",
    mcp_servers=[mcp_server],
)

result = Runner.run_sync(agent, "List one available tool.")
print(result.final_output)`,
    },
  },
  openai: {
    name: "OpenAI",
    Python: {
      code: `from traceai_openai import OpenAIInstrumentor

OpenAIInstrumentor().instrument(tracer_provider=trace_provider)`,
      sample_request_code: `import os
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

response = client.responses.create(
    model="gpt-4o-mini",
    input="Say hello in one sentence.",
)

print(response.output_text)`,
    },
    TypeScript: {
      code: `import { registerInstrumentations } from "@opentelemetry/instrumentation";
import { OpenAIInstrumentation } from "@traceai/openai";

const openaiInstrumentation = new OpenAIInstrumentation({});

registerInstrumentations({
  instrumentations: [openaiInstrumentation],
  tracerProvider,
});`,
      sample_request_code: `import OpenAI from "openai";

const openai = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
});

const response = await openai.responses.create({
  model: "gpt-4o-mini",
  input: "Say hello in one sentence.",
});

console.log(response.output_text);`,
    },
  },
  openai_agents: {
    name: "OpenAI Agents",
    Python: {
      code: `from traceai_openai_agents import OpenAIAgentsInstrumentor

OpenAIAgentsInstrumentor().instrument(tracer_provider=trace_provider)`,
      sample_request_code: `from agents import Agent, Runner

agent = Agent(
    name="Trace test",
    instructions="Answer in one short sentence.",
)

result = Runner.run_sync(agent, "Say hello.")
print(result.final_output)`,
    },
  },
};

const mergeInstrumentDefinition = (id, instrument = {}) => {
  const fallback = FALLBACK_INSTRUMENT_SNIPPETS[id] || {};
  return {
    ...fallback,
    ...instrument,
    id,
    name: instrument.name || fallback.name || id,
    Python: {
      ...(fallback.Python || {}),
      ...(instrument.Python || {}),
    },
    TypeScript: {
      ...(fallback.TypeScript || {}),
      ...(instrument.TypeScript || {}),
    },
  };
};

const defaultSampleRequestCode = ({ instrumentName, language }) => {
  const packageName = instrumentName || "your package";
  if (language === "typescript") {
    return `// Run one request in the code path that uses ${packageName}.
// Keep Future AGI open; the trace appears after the request completes.
await runYourExisting${packageName.replace(/[^A-Za-z0-9]/g, "") || "AI"}Request();`;
  }
  return `# Run one request in the code path that uses ${packageName}.
# Keep Future AGI open; the trace appears after the request completes.
run_your_existing_${
    packageName
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "") || "ai"
  }_request()`;
};

const DEFAULT_TRACE_TROUBLESHOOTING = {
  title: "If the trace does not arrive",
  checks: [
    "Confirm the Future AGI API key and secret are loaded in the process running the request.",
    "Run the request after project registration and package setup.",
    "Keep this page open, then use the first trace review step when the trace appears.",
  ],
};

const TRACE_TROUBLESHOOTING_BY_INSTRUMENT = {
  anthropic: {
    title: "If the Anthropic trace does not arrive",
    checks: [
      "Confirm ANTHROPIC_API_KEY is loaded where the request runs.",
      "Call AnthropicInstrumentor before creating the Anthropic client.",
      "Run client.messages.create once, then keep this page open for trace detection.",
    ],
  },
  bedrock: {
    title: "If the Bedrock trace does not arrive",
    checks: [
      "Confirm AWS credentials, AWS_REGION, and BEDROCK_MODEL_ID are available.",
      "Confirm the role can call bedrock:InvokeModel for the selected model.",
      "Call BedrockInstrumentor before creating or using the bedrock-runtime client.",
    ],
  },
  langchain: {
    title: "If the LangChain trace does not arrive",
    checks: [
      "Confirm the model provider key, such as OPENAI_API_KEY, is loaded.",
      "Call LangChainInstrumentor before creating ChatOpenAI or your chain.",
      "Run llm.invoke or your chain once, then watch this page for the trace.",
    ],
  },
  llama_index: {
    title: "If the LlamaIndex trace does not arrive",
    checks: [
      "Confirm the LLM or embedding provider key, such as OPENAI_API_KEY, is loaded.",
      "Call LlamaIndexInstrumentor before building the index or query engine.",
      "Run query_engine.query once so a real retrieval or generation span is created.",
    ],
  },
  mcp: {
    title: "If the MCP trace does not arrive",
    checks: [
      "Confirm MCP_SERVER_URL and MCP_SERVER_TOKEN reach a server that lists tools.",
      "Connect both OpenAI Agents and MCP before Runner.run starts.",
      "Run one safe MCP tool call, then keep this page open for trace detection.",
    ],
  },
  openai: {
    title: "If the OpenAI trace does not arrive",
    checks: [
      "Confirm OPENAI_API_KEY is loaded where the request runs.",
      "Call OpenAIInstrumentor before creating the OpenAI client.",
      "Run responses.create once, then keep this page open for trace detection.",
    ],
  },
  openai_agents: {
    title: "If the OpenAI Agents trace does not arrive",
    checks: [
      "Confirm OPENAI_API_KEY is loaded where Runner.run executes.",
      "Call OpenAIAgentsInstrumentor before constructing or running the agent.",
      "Run Runner.run or Runner.run_sync once, then continue to trace review.",
    ],
  },
};

const languageDataKey = (language) =>
  language === "typescript" ? "TypeScript" : "Python";

const instrumentSupportsLanguage = (instrument, language) =>
  Boolean(instrument?.[languageDataKey(language)]?.code);

const availableInstrumentLanguages = (instrument) =>
  ["python", "typescript"].filter((language) =>
    instrumentSupportsLanguage(instrument, language),
  );

const firstInstrumentLanguage = (instrument) =>
  availableInstrumentLanguages(instrument)[0] || "python";

const instrumentSortRank = (id) => {
  const index = SETUP_INSTRUMENT_PRIORITY.indexOf(id);
  return index === -1 ? SETUP_INSTRUMENT_PRIORITY.length : index;
};

const traceTroubleshootingForInstrument = (instrumentId) =>
  TRACE_TROUBLESHOOTING_BY_INSTRUMENT[instrumentId] ||
  DEFAULT_TRACE_TROUBLESHOOTING;

const VerificationAlert = ({ setupVerification }) => {
  if (!setupVerification) return null;

  return (
    <Alert
      data-testid="observe-setup-verification"
      severity={setupVerification.status === "ready" ? "success" : "info"}
      icon={
        setupVerification.status === "waiting" ? (
          <CircularProgress size={18} />
        ) : undefined
      }
      action={
        setupVerification.primaryAction ? (
          <Button
            color="inherit"
            size="small"
            onClick={setupVerification.primaryAction.onClick}
            disabled={setupVerification.primaryAction.disabled}
          >
            {setupVerification.primaryAction.label}
          </Button>
        ) : null
      }
      sx={{ alignItems: "center" }}
    >
      <Stack spacing={0.25}>
        <Typography variant="subtitle2">{setupVerification.title}</Typography>
        <Typography variant="body2">{setupVerification.description}</Typography>
      </Stack>
    </Alert>
  );
};

VerificationAlert.propTypes = {
  setupVerification: PropTypes.shape({
    description: PropTypes.string.isRequired,
    primaryAction: PropTypes.shape({
      disabled: PropTypes.bool,
      label: PropTypes.string.isRequired,
      onClick: PropTypes.func.isRequired,
    }),
    status: PropTypes.oneOf(["ready", "waiting"]).isRequired,
    title: PropTypes.string.isRequired,
  }),
};

const FirstTraceSetupGuide = ({
  credentialsCopied,
  completeSetupCode,
  getCodeBySection,
  instrumentCode,
  instrumentInstallCode,
  instrumentRuntimeKeyCode,
  instrumentSampleRequestCode,
  instrumentOptions,
  apiKeysHref,
  languageTab,
  onLanguageChange,
  onInstrumentChange,
  requestedInstrumentMissing,
  selectedInstrument,
  selectedInstrumentLanguage,
  setupVerification,
  tabOptions,
  tabWrapperStyles,
  theme,
}) => {
  const statusLabel =
    setupVerification?.status === "ready"
      ? "Trace detected"
      : "Checking for trace";
  const selectedInstrumentLabel = selectedInstrument?.name || "your package";
  const selectedLanguageLabel =
    selectedInstrumentLanguage === "typescript" ? "TypeScript" : "Python";
  const traceTroubleshooting = traceTroubleshootingForInstrument(
    selectedInstrument?.id,
  );

  return (
    <Box
      data-testid="observe-first-trace-guide"
      sx={{
        border: "1px solid",
        borderColor: "primary.main",
        borderRadius: 1,
        bgcolor: "action.hover",
        p: 2,
      }}
    >
      <Stack spacing={2}>
        <Stack
          direction={{ xs: "column", md: "row" }}
          spacing={1}
          justifyContent="space-between"
          alignItems={{ xs: "flex-start", md: "center" }}
        >
          <Stack spacing={0.5}>
            <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
              <Chip size="small" color="primary" label="Setup guide" />
              <Chip size="small" variant="outlined" label={statusLabel} />
            </Stack>
            <Typography variant="h6">
              Connect {selectedInstrumentLabel}, then send one trace
            </Typography>
            <Typography variant="body2" color="text.secondary" maxWidth={720}>
              These snippets match {selectedInstrumentLabel} in{" "}
              {selectedLanguageLabel}. Run one request and keep this page open;
              we move you to trace review when it arrives, then guide you to
              create the first evaluator.
            </Typography>
          </Stack>

          <TabWrapper sx={tabWrapperStyles}>
            <CustomTabs
              textColor="primary"
              value={languageTab}
              onChange={(e, value) => onLanguageChange(value)}
              TabIndicatorProps={{
                style: {
                  backgroundColor: theme.palette.primary.main,
                  opacity: 0.08,
                  height: "100%",
                  borderRadius: "8px",
                },
              }}
            >
              {tabOptions.map((tab) => (
                <CustomTab
                  key={`first-trace-${tab.value}`}
                  label={tab.label}
                  value={tab.value}
                  disabled={tab.disabled}
                />
              ))}
            </CustomTabs>
          </TabWrapper>
        </Stack>

        <Alert
          severity="info"
          icon={<Iconify icon="mdi:code-tags" width={20} />}
          data-testid="observe-package-specific-code-alert"
          sx={{ alignItems: "flex-start" }}
        >
          <Stack spacing={0.25}>
            <Typography variant="subtitle2">
              {selectedInstrumentLabel} {selectedLanguageLabel} code selected
            </Typography>
            <Typography variant="body2">
              The install, package setup, request, and trace checks below are
              for {selectedInstrumentLabel}. Switch the package if your model
              call uses another SDK.
            </Typography>
          </Stack>
        </Alert>

        {instrumentOptions.length ? (
          <Stack spacing={1}>
            <Stack spacing={0.25}>
              <Typography variant="subtitle2">
                Which package does your app use?
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Pick the library that creates the model calls. The code below
                changes to that package.
              </Typography>
            </Stack>
            <Stack
              direction="row"
              spacing={1}
              flexWrap="wrap"
              useFlexGap
              data-testid="observe-instrument-picker"
            >
              {instrumentOptions.map((instrument) => {
                const isSelected = instrument.id === selectedInstrument?.id;
                return (
                  <Button
                    key={instrument.id}
                    size="small"
                    variant={isSelected ? "contained" : "outlined"}
                    color={isSelected ? "primary" : "inherit"}
                    onClick={() => onInstrumentChange(instrument.id)}
                    data-testid={`observe-instrument-option-${instrument.id}`}
                    startIcon={
                      instrument.logo ? (
                        <Box
                          component="img"
                          src={instrument.logo}
                          alt=""
                          sx={{ width: 18, height: 18, objectFit: "contain" }}
                        />
                      ) : null
                    }
                    sx={{ textTransform: "none" }}
                  >
                    {instrument.name}
                  </Button>
                );
              })}
            </Stack>
          </Stack>
        ) : null}

        {requestedInstrumentMissing ? (
          <Alert severity="warning" sx={{ alignItems: "center" }}>
            <Typography variant="body2">
              The requested package is not available in this setup response.
              Showing the default package setup instead.
            </Typography>
          </Alert>
        ) : null}

        <Box
          data-testid="observe-first-trace-steps"
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "1fr",
              md: "repeat(2, minmax(0, 1fr))",
              xl: "repeat(4, minmax(0, 1fr))",
            },
            gap: 1,
          }}
        >
          {FIRST_TRACE_STEPS.map((step, index) => (
            <Box
              key={step.id}
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                bgcolor: "background.paper",
                p: 1.25,
                minHeight: 96,
              }}
            >
              <Stack spacing={0.75}>
                <Stack direction="row" spacing={0.75} alignItems="center">
                  <Chip size="small" label={index + 1} />
                  <Typography variant="subtitle2">{step.label}</Typography>
                </Stack>
                <Typography variant="body2" color="text.secondary">
                  {step.description}
                </Typography>
              </Stack>
            </Box>
          ))}
        </Box>

        <Stack spacing={1} sx={{ minWidth: 0 }}>
          <Typography variant="subtitle2">
            Copy complete {selectedInstrumentLabel} {selectedLanguageLabel}{" "}
            example
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Use this as a scratch file or adapt the same blocks into your app.
            It includes Future AGI keys, provider keys, project registration,
            package setup, and one request.
          </Typography>
          <InstructionCodeCopy
            ariaLabel="Copy complete package setup"
            text={completeSetupCode}
            language={selectedInstrumentLanguage}
          />
        </Stack>

        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", lg: "repeat(2, 1fr)" },
            gap: 1.5,
            minWidth: 0,
          }}
        >
          <Stack spacing={1} sx={{ minWidth: 0 }}>
            <Typography variant="subtitle2">
              1. Install {selectedInstrumentLabel}
            </Typography>
            <InstructionCodeCopy
              ariaLabel="Copy install command"
              text={instrumentInstallCode}
              language={selectedInstrumentLanguage}
            />
          </Stack>
          <Stack spacing={1} sx={{ minWidth: 0 }}>
            <Stack
              direction={{ xs: "column", sm: "row" }}
              spacing={1}
              alignItems={{ xs: "flex-start", sm: "center" }}
              justifyContent="space-between"
            >
              <Typography variant="subtitle2">
                2. Load Future AGI and provider keys
              </Typography>
              <Button
                size="small"
                variant="outlined"
                component={RouterLink}
                href={apiKeysHref}
                startIcon={<Iconify icon="mdi:key-outline" width={16} />}
                sx={{ alignSelf: { xs: "stretch", sm: "flex-start" } }}
              >
                {credentialsCopied ? "Create another key" : "Create API key"}
              </Button>
            </Stack>
            {credentialsCopied ? (
              <Alert severity="success" icon={false} sx={{ py: 0.5 }}>
                <Typography variant="caption">
                  Credentials copied. Paste both values into the snippet, then
                  run one request.
                </Typography>
              </Alert>
            ) : (
              <Typography variant="caption" color="text.secondary">
                Create a Future AGI API key and secret key before running the
                snippet.
              </Typography>
            )}
            <InstructionCodeCopy
              ariaLabel="Copy project keys"
              text={getCodeBySection("keys")}
              language={languageTab}
            />
            {instrumentRuntimeKeyCode ? (
              <>
                <Typography variant="caption" color="text.secondary">
                  Also load the {selectedInstrumentLabel} runtime key in the
                  same shell or process that runs the request.
                </Typography>
                <InstructionCodeCopy
                  ariaLabel={`Copy ${selectedInstrumentLabel} runtime keys`}
                  text={instrumentRuntimeKeyCode}
                  language="bash"
                />
              </>
            ) : null}
            <InstructionCodeCopy
              ariaLabel="Copy project registration"
              text={getCodeBySection("projectAddCode")}
              language={languageTab}
            />
          </Stack>
          <Stack spacing={1} sx={{ minWidth: 0 }}>
            <Typography variant="subtitle2">
              3. Connect {selectedInstrumentLabel}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Paste this before importing or creating the client, then run one
              request in your app.
            </Typography>
            <InstructionCodeCopy
              ariaLabel="Copy package setup code"
              text={instrumentCode}
              language={selectedInstrumentLanguage}
            />
          </Stack>
          <Stack spacing={1} sx={{ minWidth: 0 }}>
            <Typography variant="subtitle2">
              4. Run one {selectedInstrumentLabel} request
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Use this ready-to-run request if you do not have a local request
              ready. Keep this page open after it runs; Future AGI waits for the
              trace, opens review when it arrives, then points you to evaluator
              setup.
            </Typography>
            <InstructionCodeCopy
              ariaLabel="Copy package request"
              text={instrumentSampleRequestCode}
              language={selectedInstrumentLanguage}
            />
          </Stack>
        </Box>

        <VerificationAlert setupVerification={setupVerification} />

        <Alert
          severity="info"
          icon={<Iconify icon="mdi:help-circle-outline" width={20} />}
          data-testid="observe-trace-troubleshooting"
          sx={{ alignItems: "flex-start" }}
        >
          <Stack spacing={0.75}>
            <Typography variant="subtitle2">
              {traceTroubleshooting.title}
            </Typography>
            <Stack spacing={0.5}>
              {traceTroubleshooting.checks.map((check) => (
                <Typography key={check} variant="body2">
                  {check}
                </Typography>
              ))}
            </Stack>
          </Stack>
        </Alert>
      </Stack>
    </Box>
  );
};

FirstTraceSetupGuide.propTypes = {
  completeSetupCode: PropTypes.string.isRequired,
  credentialsCopied: PropTypes.bool,
  getCodeBySection: PropTypes.func.isRequired,
  instrumentCode: PropTypes.string.isRequired,
  instrumentInstallCode: PropTypes.string.isRequired,
  instrumentRuntimeKeyCode: PropTypes.string,
  instrumentSampleRequestCode: PropTypes.string.isRequired,
  instrumentOptions: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      logo: PropTypes.string,
      name: PropTypes.string.isRequired,
    }),
  ).isRequired,
  apiKeysHref: PropTypes.string.isRequired,
  languageTab: PropTypes.string.isRequired,
  onLanguageChange: PropTypes.func.isRequired,
  onInstrumentChange: PropTypes.func.isRequired,
  requestedInstrumentMissing: PropTypes.bool,
  selectedInstrument: PropTypes.object,
  selectedInstrumentLanguage: PropTypes.string.isRequired,
  setupVerification: VerificationAlert.propTypes.setupVerification,
  tabOptions: PropTypes.arrayOf(
    PropTypes.shape({
      disabled: PropTypes.bool,
      label: PropTypes.string.isRequired,
      value: PropTypes.string.isRequired,
    }),
  ).isRequired,
  tabWrapperStyles: PropTypes.object.isRequired,
  theme: PropTypes.object.isRequired,
};

const NewObserve = ({ setupVerification, showFirstTraceGuide = false }) => {
  const theme = useTheme();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedLanguage = normalizeSetupLanguage(
    searchParams.get("language") || searchParams.get("lang"),
  );
  const initialLanguage = requestedLanguage || "python";
  const [languageTab, setLanguageTab] = useState(initialLanguage);
  const credentialsCopied =
    searchParams.get("credential_step") === "done" &&
    ONBOARDING_SOURCE_VALUES.has(searchParams.get("source"));
  const requestedInstrumentId = normalizeInstrumentId(
    searchParams.get("instrument") ||
      searchParams.get("package") ||
      searchParams.get("provider"),
  );
  const quickStartAttribution = useMemo(
    () => ({
      quickStartGoal: searchParams.get("quick_start_goal"),
      quickStartId: searchParams.get("quick_start_id"),
      quickStartPrimaryPath: searchParams.get("quick_start_primary_path"),
    }),
    [searchParams],
  );
  const [selectedInstrumentId, setSelectedInstrumentId] = useState(
    requestedInstrumentId || null,
  );
  const requestedInstrumentAppliedRef = useRef(null);
  const {
    data: keysData,
    isSuccess,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["keys"],
    queryFn: () =>
      axios.get(endpoints.project.getCodeBlockTracer, {
        params: {
          project_type: "observe",
        },
      }),
    select: (d) => d.data?.result,
  });

  const tabOptions = [
    { label: "Python", value: "python", disabled: false },
    { label: "TypeScript", value: "typescript", disabled: false },
  ];

  const tabWrapperStyles = useMemo(
    () => ({
      marginBottom: 0,
      alignSelf: "flex-start",
    }),
    [],
  );

  const cleanCode = (code) => {
    if (typeof code !== "string") return "Code not available";
    return code.replace(/^\n+/, "").replace(/\n+$/, "");
  };

  // Helper functions to get the correct code based on active tabs
  const getCodeBySection = (section) => {
    const languageKey = languageTab === "python" ? "Python" : "TypeScript";
    const sectionData =
      keysData?.[section] || keysData?.[CODE_SECTION_ALIASES[section]];
    return cleanCode(sectionData?.[languageKey]);
  };

  const instrumentOptions = useMemo(() => {
    const mergedInstruments = new Map(
      Object.keys(FALLBACK_INSTRUMENT_SNIPPETS).map((id) => [
        id,
        mergeInstrumentDefinition(id),
      ]),
    );
    Object.entries(keysData?.instruments || {}).forEach(([id, instrument]) => {
      const normalizedId = normalizeInstrumentId(id);
      if (!normalizedId || !instrument || typeof instrument !== "object") {
        return;
      }
      mergedInstruments.set(
        normalizedId,
        mergeInstrumentDefinition(normalizedId, instrument),
      );
    });

    return Array.from(mergedInstruments.values())
      .filter((instrument) => availableInstrumentLanguages(instrument).length)
      .sort((left, right) => {
        const rankDiff =
          instrumentSortRank(left.id) - instrumentSortRank(right.id);
        if (rankDiff !== 0) return rankDiff;
        return String(left.name).localeCompare(String(right.name));
      });
  }, [keysData?.instruments]);
  const requestedInstrumentMissing = Boolean(
    requestedInstrumentId &&
      instrumentOptions.length &&
      !instrumentOptions.some(
        (instrument) => instrument.id === requestedInstrumentId,
      ),
  );
  const selectedInstrument = useMemo(() => {
    if (!instrumentOptions.length) return null;
    return (
      instrumentOptions.find(
        (instrument) => instrument.id === selectedInstrumentId,
      ) || instrumentOptions[0]
    );
  }, [instrumentOptions, selectedInstrumentId]);
  const selectedInstrumentLanguage = instrumentSupportsLanguage(
    selectedInstrument,
    languageTab,
  )
    ? languageTab
    : firstInstrumentLanguage(selectedInstrument);
  const selectedInstrumentLanguageKey = languageDataKey(
    selectedInstrumentLanguage,
  );
  const selectedInstrumentCode =
    selectedInstrument?.[selectedInstrumentLanguageKey]?.code;
  const instrumentCode = cleanCode(
    selectedInstrumentCode ||
      `Package setup code is not available for ${selectedInstrument?.name || "this package"} in ${
        selectedInstrumentLanguage === "typescript" ? "TypeScript" : "Python"
      }. Choose another package or language.`,
  );
  const instrumentInstallCode =
    INSTRUMENT_INSTALL_COMMANDS[selectedInstrumentLanguage]?.[
      selectedInstrument?.id
    ] || getCodeBySection("installationGuide");
  const instrumentRuntimeKeyCode = runtimeKeySetupCode(
    selectedInstrument?.id,
    selectedInstrumentLanguage,
  );
  const instrumentSampleRequestCode = cleanCode(
    selectedInstrument?.[selectedInstrumentLanguageKey]?.sample_request_code ||
      defaultSampleRequestCode({
        instrumentName: selectedInstrument?.name,
        language: selectedInstrumentLanguage,
      }),
  );
  const completeSetupCode = cleanCode(
    [
      getCodeBySection("keys"),
      runtimeKeySetupCode(selectedInstrument?.id, selectedInstrumentLanguage),
      getCodeBySection("projectAddCode"),
      instrumentCode,
      instrumentSampleRequestCode,
    ]
      .filter(Boolean)
      .join("\n\n"),
  );
  const apiKeysHref = apiKeysOnboardingHref({
    instrumentId: selectedInstrument?.id,
    language: selectedInstrumentLanguage,
    quickStartAttribution,
  });
  const firstTraceTabOptions = tabOptions.map((tab) => ({
    ...tab,
    disabled: selectedInstrument
      ? !instrumentSupportsLanguage(selectedInstrument, tab.value)
      : tab.disabled,
  }));

  const syncSetupIntentToUrl = useCallback(
    ({ instrumentId, language } = {}) => {
      if (!showFirstTraceGuide || !instrumentId) return;

      const nextParams = new URLSearchParams(searchParams);
      const isSetupRoute =
        nextParams.get("setup") === "true" ||
        ONBOARDING_SOURCE_VALUES.has(nextParams.get("source")) ||
        Boolean(nextParams.get("journey_step"));
      if (!isSetupRoute) return;

      nextParams.set("provider", instrumentId);
      if (LANGUAGE_VALUES.has(language)) {
        nextParams.set("language", language);
      }

      if (nextParams.toString() !== searchParams.toString()) {
        setSearchParams(nextParams, { replace: true });
      }
    },
    [searchParams, setSearchParams, showFirstTraceGuide],
  );

  useEffect(() => {
    if (!instrumentOptions.length) return;
    const requestedOption = instrumentOptions.find(
      (instrument) => instrument.id === requestedInstrumentId,
    );
    const currentOption = instrumentOptions.find(
      (instrument) => instrument.id === selectedInstrumentId,
    );
    if (
      requestedOption &&
      requestedInstrumentAppliedRef.current !== requestedInstrumentId
    ) {
      requestedInstrumentAppliedRef.current = requestedInstrumentId;
      setSelectedInstrumentId(requestedOption.id);
      return;
    }
    if (!currentOption) {
      setSelectedInstrumentId(instrumentOptions[0].id);
    }
  }, [instrumentOptions, requestedInstrumentId, selectedInstrumentId]);

  useEffect(() => {
    if (!selectedInstrument) return;
    if (instrumentSupportsLanguage(selectedInstrument, languageTab)) return;
    setLanguageTab(firstInstrumentLanguage(selectedInstrument));
  }, [languageTab, selectedInstrument]);

  useEffect(() => {
    if (!selectedInstrument) return;
    syncSetupIntentToUrl({
      instrumentId: selectedInstrument.id,
      language: selectedInstrumentLanguage,
    });
  }, [selectedInstrument, selectedInstrumentLanguage, syncSetupIntentToUrl]);

  useEffect(() => {
    if (!showFirstTraceGuide || !selectedInstrument) return;
    persistObserveSetupIntent({
      setupLanguage: selectedInstrumentLanguage,
      setupProvider: selectedInstrument.id,
    });
  }, [selectedInstrument, selectedInstrumentLanguage, showFirstTraceGuide]);

  const handleInstrumentChange = (instrumentId) => {
    const nextInstrument = instrumentOptions.find(
      (instrument) => instrument.id === instrumentId,
    );
    const nextLanguage =
      nextInstrument && !instrumentSupportsLanguage(nextInstrument, languageTab)
        ? firstInstrumentLanguage(nextInstrument)
        : languageTab;
    setSelectedInstrumentId(instrumentId);
    if (nextLanguage !== languageTab) {
      setLanguageTab(nextLanguage);
    }
    syncSetupIntentToUrl({ instrumentId, language: nextLanguage });
  };

  const handleLanguageChange = (nextLanguage) => {
    setLanguageTab(nextLanguage);
    syncSetupIntentToUrl({
      instrumentId: selectedInstrument?.id || selectedInstrumentId,
      language: nextLanguage,
    });
  };

  return (
    <Box
      sx={{
        width: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 4, // 32px spacing between major sections
      }}
    >
      {setupVerification &&
      (!showFirstTraceGuide || !isSuccess || !keysData) ? (
        <VerificationAlert setupVerification={setupVerification} />
      ) : null}

      {!isSuccess || !keysData ? <LinearProgress /> : null}

      {isSuccess && keysData ? (
        <>
          {showFirstTraceGuide ? (
            <FirstTraceSetupGuide
              completeSetupCode={completeSetupCode}
              credentialsCopied={credentialsCopied}
              getCodeBySection={getCodeBySection}
              instrumentCode={instrumentCode}
              instrumentInstallCode={instrumentInstallCode}
              instrumentRuntimeKeyCode={instrumentRuntimeKeyCode}
              instrumentSampleRequestCode={instrumentSampleRequestCode}
              instrumentOptions={instrumentOptions}
              apiKeysHref={apiKeysHref}
              languageTab={languageTab}
              onLanguageChange={handleLanguageChange}
              onInstrumentChange={handleInstrumentChange}
              requestedInstrumentMissing={requestedInstrumentMissing}
              selectedInstrument={selectedInstrument}
              selectedInstrumentLanguage={selectedInstrumentLanguage}
              setupVerification={setupVerification}
              tabOptions={firstTraceTabOptions}
              tabWrapperStyles={tabWrapperStyles}
              theme={theme}
            />
          ) : null}

          {!showFirstTraceGuide ? (
            <>
              {/* Installation & Keys Section */}
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
                <InstructionTitle
                  title="Install Dependencies"
                  description="For more instructions, checkout our "
                  url="https://docs.futureagi.com/docs/observe"
                  urltext="Docs"
                  onUrlClick={() => handleOnDocsClicked("observe_page")}
                />

                <InstructionTitle description="Configure your application to send traces to Future AGI" />

                {/* Tab selector for installation */}
                <TabWrapper sx={tabWrapperStyles}>
                  <CustomTabs
                    textColor="primary"
                    value={languageTab}
                    onChange={(e, value) => setLanguageTab(value)}
                    TabIndicatorProps={{
                      style: {
                        backgroundColor: theme.palette.primary.main,
                        opacity: 0.08,
                        height: "100%",
                        borderRadius: "8px",
                      },
                    }}
                  >
                    {tabOptions.map((tab) => (
                      <CustomTab
                        key={`config-${tab.value}`}
                        label={tab.label}
                        value={tab.value}
                        disabled={tab.disabled}
                      />
                    ))}
                  </CustomTabs>
                </TabWrapper>

                <InstructionCodeCopy
                  ariaLabel="Copy install command"
                  text={getCodeBySection("installationGuide")}
                  language={languageTab}
                  // onCopy={() => trackEvent(Events.installDependenciesCopied)}
                />

                {/* API Keys */}
                <Box sx={{ mt: 1.5 }}>
                  <InstructionTitle
                    title="Load API keys"
                    description="load your API keys"
                  />
                </Box>

                <InstructionCodeCopy
                  ariaLabel="Copy API keys"
                  text={getCodeBySection("keys")}
                  language={languageTab}
                  onCopy={() => trackEvent(Events.apikeys)}
                />
              </Box>

              {/* Project registration section with its own tab control */}
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
                <InstructionTitle
                  title="Register project"
                  description="Register your application before the package call so traces are sent to this project."
                />

                {/* Separate tab selector for project registration */}
                <TabWrapper sx={tabWrapperStyles}>
                  <CustomTabs
                    textColor="primary"
                    value={languageTab}
                    onChange={(e, value) => setLanguageTab(value)}
                    TabIndicatorProps={{
                      style: {
                        backgroundColor: theme.palette.primary.main,
                        opacity: 0.08,
                        height: "100%",
                        borderRadius: "8px",
                      },
                    }}
                  >
                    {tabOptions.map((tab) => (
                      <CustomTab
                        key={`telemetry-${tab.value}`}
                        label={tab.label}
                        value={tab.value}
                        disabled={tab.disabled}
                      />
                    ))}
                  </CustomTabs>
                </TabWrapper>

                <InstructionCodeCopy
                  ariaLabel="Copy project registration"
                  text={getCodeBySection("projectAddCode")}
                  language={languageTab}
                  // onCopy={() => trackEvent(Events.setupTelemetryCopied)}
                />
              </Box>

              {/* Package setup section */}
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
                <InstructionTitle
                  title="Connect package calls"
                  description="Connect the package that makes AI calls so Future AGI can trace the request."
                />

                <ObserveInstruments
                  data={keysData?.instruments}
                  isLoading={isLoading}
                  isSuccess={isSuccess}
                  error={error}
                  languageTab={languageTab}
                  onLanguageChange={setLanguageTab}
                />
              </Box>
            </>
          ) : null}
        </>
      ) : null}
    </Box>
  );
};

NewObserve.propTypes = {
  showFirstTraceGuide: PropTypes.bool,
  setupVerification: PropTypes.shape({
    description: PropTypes.string.isRequired,
    primaryAction: PropTypes.shape({
      disabled: PropTypes.bool,
      label: PropTypes.string.isRequired,
      onClick: PropTypes.func.isRequired,
    }),
    status: PropTypes.oneOf(["ready", "waiting"]).isRequired,
    title: PropTypes.string.isRequired,
  }),
};

export default NewObserve;

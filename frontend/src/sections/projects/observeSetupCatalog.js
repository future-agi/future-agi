export const OBSERVE_SETUP_LANGUAGE_VALUES = new Set(["python", "typescript"]);

const OBSERVE_SETUP_PACKAGES = {
  anthropic: {
    id: "anthropic",
    instrumentId: "anthropic",
    label: "Anthropic",
    install: {
      python: "pip install traceAI-anthropic anthropic",
      typescript:
        "npm install @traceai/fi-core @traceai/anthropic @opentelemetry/instrumentation @anthropic-ai/sdk",
    },
    runtimeKeys: ["ANTHROPIC_API_KEY"],
    snippets: {
      Python: {
        code: `from traceai_anthropic import AnthropicInstrumentor

AnthropicInstrumentor().instrument(tracer_provider=trace_provider)`,
        sample_request_code: `import os
import anthropic

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

message = client.messages.create(
    model=os.environ.get("ANTHROPIC_MODEL", "your-anthropic-model"),
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
  model: process.env.ANTHROPIC_MODEL || "your-anthropic-model",
  max_tokens: 256,
  messages: [{ role: "user", content: "Say hello in one sentence." }],
});

console.log(message.content);`,
      },
    },
    troubleshooting: {
      title: "If the Anthropic trace does not arrive",
      checks: [
        "Confirm ANTHROPIC_API_KEY is loaded where the request runs.",
        "Call AnthropicInstrumentor before creating the Anthropic client.",
        "Run client.messages.create once, then keep this page open for trace detection.",
      ],
    },
  },
  bedrock: {
    id: "bedrock",
    instrumentId: "bedrock",
    label: "Bedrock",
    install: {
      python: "pip install traceAI-bedrock boto3",
    },
    runtimeKeys: [
      "AWS_ACCESS_KEY_ID",
      "AWS_SECRET_ACCESS_KEY",
      "AWS_REGION",
      "BEDROCK_MODEL_ID",
    ],
    snippets: {
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
    troubleshooting: {
      title: "If the Bedrock trace does not arrive",
      checks: [
        "Confirm AWS credentials, AWS_REGION, and BEDROCK_MODEL_ID are available.",
        "Confirm the role can call bedrock:InvokeModel for the selected model.",
        "Call BedrockInstrumentor before creating or using the bedrock-runtime client.",
      ],
    },
  },
  langchain: {
    id: "langchain",
    instrumentId: "langchain",
    label: "LangChain",
    install: {
      python: "pip install traceAI-langchain langchain-openai",
      typescript:
        "npm install @traceai/fi-core @traceai/langchain @opentelemetry/instrumentation",
    },
    runtimeKeys: ["OPENAI_API_KEY"],
    snippets: {
      Python: {
        code: `from traceai_langchain import LangChainInstrumentor

LangChainInstrumentor().instrument(tracer_provider=trace_provider)`,
        sample_request_code: `from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4o-mini")
response = llm.invoke("Say hello in one sentence.")

print(response.content)`,
      },
    },
    troubleshooting: {
      title: "If the LangChain trace does not arrive",
      checks: [
        "Confirm the model provider key, such as OPENAI_API_KEY, is loaded.",
        "Call LangChainInstrumentor before creating ChatOpenAI or your chain.",
        "Run llm.invoke or your chain once, then watch this page for the trace.",
      ],
    },
  },
  llamaindex: {
    id: "llamaindex",
    instrumentId: "llama_index",
    label: "LlamaIndex",
    install: {
      python: "pip install traceAI-llamaindex llama-index",
    },
    runtimeKeys: ["OPENAI_API_KEY"],
    snippets: {
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
    troubleshooting: {
      title: "If the LlamaIndex trace does not arrive",
      checks: [
        "Confirm the LLM or embedding provider key, such as OPENAI_API_KEY, is loaded.",
        "Call LlamaIndexInstrumentor before building the index or query engine.",
        "Run query_engine.query once so a real retrieval or generation span is created.",
      ],
    },
  },
  mcp: {
    id: "mcp",
    instrumentId: "mcp",
    label: "MCP",
    install: {
      python: "pip install traceAI-mcp traceAI-openai-agents openai-agents",
      typescript:
        "npm install @traceai/fi-core @traceai/mcp @opentelemetry/instrumentation",
    },
    runtimeKeys: ["OPENAI_API_KEY", "MCP_SERVER_URL", "MCP_SERVER_TOKEN"],
    snippets: {
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
    troubleshooting: {
      title: "If the MCP trace does not arrive",
      checks: [
        "Confirm MCP_SERVER_URL and MCP_SERVER_TOKEN reach a server that lists tools.",
        "Connect both OpenAI Agents and MCP before Runner.run starts.",
        "Run one safe MCP tool call, then keep this page open for trace detection.",
      ],
    },
  },
  openai: {
    id: "openai",
    instrumentId: "openai",
    label: "OpenAI",
    install: {
      python: "pip install traceAI-openai openai",
      typescript:
        "npm install @traceai/fi-core @traceai/openai @opentelemetry/instrumentation openai",
    },
    runtimeKeys: ["OPENAI_API_KEY"],
    snippets: {
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
    troubleshooting: {
      title: "If the OpenAI trace does not arrive",
      checks: [
        "Confirm OPENAI_API_KEY is loaded where the request runs.",
        "Call OpenAIInstrumentor before creating the OpenAI client.",
        "Run responses.create once, then keep this page open for trace detection.",
      ],
    },
  },
  openai_agents: {
    id: "openai_agents",
    instrumentId: "openai_agents",
    label: "OpenAI Agents",
    install: {
      python: "pip install traceAI-openai-agents openai-agents",
      typescript:
        "npm install @traceai/fi-core @traceai/openai-agents @opentelemetry/instrumentation",
    },
    runtimeKeys: ["OPENAI_API_KEY"],
    snippets: {
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
    troubleshooting: {
      title: "If the OpenAI Agents trace does not arrive",
      checks: [
        "Confirm OPENAI_API_KEY is loaded where Runner.run executes.",
        "Call OpenAIAgentsInstrumentor before constructing or running the agent.",
        "Run Runner.run or Runner.run_sync once, then continue to trace review.",
      ],
    },
  },
};

const OBSERVE_SETUP_PROVIDER_ALIASES = {
  "llama-index": "llamaindex",
  llama_index: "llamaindex",
  "openai-agents": "openai_agents",
  openaiagents: "openai_agents",
};

const DEFAULT_TRACE_TROUBLESHOOTING = {
  title: "If the trace does not arrive",
  checks: [
    "Confirm the Future AGI API key and secret are loaded in the process running the request.",
    "Run the request after project registration and package setup.",
    "Keep this page open, then use the first trace review step when the trace appears.",
  ],
};

const OBSERVE_SETUP_PROVIDER_IDS = [
  "openai",
  "anthropic",
  "langchain",
  "openai_agents",
  "llamaindex",
  "bedrock",
  "mcp",
];
const OBSERVE_SETUP_PACKAGES_BY_INSTRUMENT_ID = Object.fromEntries(
  Object.values(OBSERVE_SETUP_PACKAGES).map((entry) => [
    entry.instrumentId,
    entry,
  ]),
);

export const normalizeObserveSetupValue = (value) =>
  typeof value === "string"
    ? value.trim().toLowerCase().replaceAll("-", "_")
    : "";

export const normalizeObserveSetupLanguage = (value) => {
  const normalizedValue = normalizeObserveSetupValue(value);
  return OBSERVE_SETUP_LANGUAGE_VALUES.has(normalizedValue)
    ? normalizedValue
    : "";
};

export const normalizeObserveSetupProvider = (value) => {
  const normalizedValue = normalizeObserveSetupValue(value);
  const canonicalValue =
    OBSERVE_SETUP_PROVIDER_ALIASES[normalizedValue] || normalizedValue;
  return OBSERVE_SETUP_PACKAGES[canonicalValue] ? canonicalValue : null;
};

export const normalizeObserveInstrumentId = (value) => {
  const canonicalProvider = normalizeObserveSetupProvider(value);
  if (canonicalProvider) {
    return OBSERVE_SETUP_PACKAGES[canonicalProvider].instrumentId;
  }
  return normalizeObserveSetupValue(value);
};

const observeSetupPackageFromProvider = (provider) =>
  OBSERVE_SETUP_PACKAGES[normalizeObserveSetupProvider(provider)] || null;

const observeSetupPackageFromInstrumentId = (instrumentId) => {
  const normalizedInstrumentId = normalizeObserveInstrumentId(instrumentId);
  return (
    OBSERVE_SETUP_PACKAGES_BY_INSTRUMENT_ID[normalizedInstrumentId] ||
    observeSetupPackageFromProvider(instrumentId)
  );
};

export const getObserveSetupProviderIds = () => [...OBSERVE_SETUP_PROVIDER_IDS];

export const getObserveSetupPackageOptions = () =>
  OBSERVE_SETUP_PROVIDER_IDS.map((id) => {
    const entry = OBSERVE_SETUP_PACKAGES[id];
    return {
      id,
      label: entry.label,
      languages: ["python", "typescript"].filter(
        (language) => entry.snippets[observeLanguageDataKey(language)]?.code,
      ),
    };
  });

export const getObserveSetupProviderLabel = (setupProvider) =>
  observeSetupPackageFromProvider(setupProvider)?.label || "";

export const getObserveSetupPackageLabel = ({
  setupLanguage,
  setupProvider,
} = {}) => {
  const providerLabel = getObserveSetupProviderLabel(setupProvider);
  if (!providerLabel) return "";
  const language = normalizeObserveSetupLanguage(setupLanguage);
  const languageLabel = language === "typescript" ? "TypeScript" : "Python";
  return [providerLabel, language ? languageLabel : ""]
    .filter(Boolean)
    .join(" ");
};

export const getObservePackageInstallCommand = ({
  setupLanguage,
  setupProvider,
} = {}) => {
  const entry = observeSetupPackageFromProvider(setupProvider);
  const language = normalizeObserveSetupLanguage(setupLanguage);
  if (!entry || !language) return "";
  return entry.install[language] || "";
};

export const getObservePackageSampleRequestCode = ({
  setupLanguage,
  setupProvider,
} = {}) => {
  const entry = observeSetupPackageFromProvider(setupProvider);
  const language = normalizeObserveSetupLanguage(setupLanguage);
  if (!entry || !language) return "";
  const languageKey = observeLanguageDataKey(language);
  return entry.snippets[languageKey]?.sample_request_code || "";
};

export const getObserveInstrumentInstallCommand = ({
  instrumentId,
  language,
} = {}) => {
  const entry = observeSetupPackageFromInstrumentId(instrumentId);
  const normalizedLanguage = normalizeObserveSetupLanguage(language);
  if (!entry || !normalizedLanguage) return "";
  return entry.install[normalizedLanguage] || "";
};

export const getObserveRuntimeKeySetupCode = (
  instrumentId,
  language = "bash",
) => {
  const entry = observeSetupPackageFromInstrumentId(instrumentId);
  const keys = entry?.runtimeKeys || [];
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

export const getObserveFallbackInstrumentDefinitions = () =>
  Object.fromEntries(
    Object.values(OBSERVE_SETUP_PACKAGES).map((entry) => [
      entry.instrumentId,
      {
        name: entry.label,
        ...entry.snippets,
      },
    ]),
  );

export const mergeObserveInstrumentDefinition = (id, instrument = {}) => {
  const fallback = getObserveFallbackInstrumentDefinitions()[id] || {};
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

export const defaultObserveSampleRequestCode = ({
  instrumentName,
  language,
}) => {
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

export const observeLanguageDataKey = (language) =>
  language === "typescript" ? "TypeScript" : "Python";

export const observeInstrumentSupportsLanguage = (instrument, language) =>
  Boolean(instrument?.[observeLanguageDataKey(language)]?.code);

export const observeAvailableInstrumentLanguages = (instrument) =>
  ["python", "typescript"].filter((language) =>
    observeInstrumentSupportsLanguage(instrument, language),
  );

export const observeFirstInstrumentLanguage = (instrument) =>
  observeAvailableInstrumentLanguages(instrument)[0] || "python";

const SETUP_INSTRUMENT_PRIORITY = [
  "openai",
  "anthropic",
  "langchain",
  "openai_agents",
  "llama_index",
  "bedrock",
  "mcp",
];

export const observeInstrumentSortRank = (id) => {
  const index = SETUP_INSTRUMENT_PRIORITY.indexOf(id);
  return index === -1 ? SETUP_INSTRUMENT_PRIORITY.length : index;
};

export const getObserveTraceTroubleshooting = (instrumentId) =>
  observeSetupPackageFromInstrumentId(instrumentId)?.troubleshooting ||
  DEFAULT_TRACE_TROUBLESHOOTING;

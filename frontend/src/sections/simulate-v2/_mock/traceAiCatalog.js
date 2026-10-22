/*
  traceAI as Observe's New project drawer shows it (dev: tracer/utils/constants.py,
  served by /tracer/project/project_sdk_code/): install, load the API keys,
  register the project, then one instrumentor per framework. Generated from
  dev's INSTALLATION_GUIDE, OBSERVE_CODEBLOCK and INSTRUMENTORS — keep the
  snippets as Observe has them.
*/

export const TRACE_AI_INSTALL = {
  python: "pip install fi-instrumentation-otel",
  typescript: "npm install @traceai/fi-core",
};

export const TRACE_AI_INSTRUMENTORS = [
 {
  "id": "langchain",
  "name": "LangChain",
  "python": {
   "code": "from traceai_langchain import LangChainInstrumentor\n\nLangChainInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-langchain"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/langchain"
  }
 },
 {
  "id": "openai",
  "name": "OpenAI",
  "python": {
   "code": "from traceai_openai import OpenAIInstrumentor\n\nOpenAIInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-openai"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/openai"
  },
  "typescript": {
   "code": "import { OpenAIInstrumentation } from \"@traceai/openai\";\nimport { registerInstrumentations } from \"@opentelemetry/instrumentation\";\n\nconst openaiInstrumentation = new OpenAIInstrumentation({});\n\nregisterInstrumentations({\n  instrumentations: [openaiInstrumentation],\n  tracerProvider: tracerProvider,\n});",
   "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_openai"
  }
 },
 {
  "id": "anthropic",
  "name": "Anthropic",
  "python": {
   "code": "from traceai_anthropic import AnthropicInstrumentor\n\nAnthropicInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-anthropic"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/anthropic"
  },
  "typescript": {
   "code": "import { AnthropicInstrumentation } from \"@traceai/anthropic\";\nimport { registerInstrumentations } from \"@opentelemetry/instrumentation\";\n\nconst anthropicInstrumentation = new AnthropicInstrumentation({});\n\nregisterInstrumentations({\n  instrumentations: [anthropicInstrumentation],\n  tracerProvider: tracerProvider,\n});",
   "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_anthropic"
  }
 },
 {
  "id": "mcp",
  "name": "MCP (Model Context Protocol)",
  "python": {
   "code": "from traceai_openai_agents import OpenAIAgentsInstrumentor\nfrom traceai_mcp import MCPInstrumentor\n\nOpenAIAgentsInstrumentor().instrument(tracer_provider=trace_provider)\nMCPInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-mcp",
    "traceAI-openai-agents"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/mcp"
  }
 },
 {
  "id": "bedrock",
  "name": "Bedrock",
  "python": {
   "code": "from traceai_bedrock import BedrockInstrumentor\n\nBedrockInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-bedrock"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/bedrock"
  }
 },
 {
  "id": "crewai",
  "name": "CrewAI",
  "python": {
   "code": "from traceai_crewai import CrewAIInstrumentor\n\nCrewAIInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-crewai"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/crewai"
  }
 },
 {
  "id": "dspy",
  "name": "DSPy",
  "python": {
   "code": "from traceai_dspy import DSPyInstrumentor\n\nDSPyInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-dspy"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/dspy"
  }
 },
 {
  "id": "groq",
  "name": "Groq",
  "python": {
   "code": "from traceai_groq import GroqInstrumentor\n\nGroqInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-groq"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/groq"
  }
 },
 {
  "id": "haystack",
  "name": "Haystack",
  "python": {
   "code": "from traceai_haystack import HaystackInstrumentor\n\nHaystackInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-haystack"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/haystack"
  }
 },
 {
  "id": "instructor",
  "name": "Instructor",
  "python": {
   "code": "from traceai_instructor import InstructorInstrumentor\n\nInstructorInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-instructor"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/instructor"
  }
 },
 {
  "id": "litellm",
  "name": "LiteLLM",
  "python": {
   "code": "from traceai_litellm import LiteLLMInstrumentor\n\nLiteLLMInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-litellm"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/litellm"
  }
 },
 {
  "id": "llama_index",
  "name": "LlamaIndex",
  "python": {
   "code": "from traceai_llamaindex import LlamaIndexInstrumentor\n\nLlamaIndexInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-llamaindex"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/llama_index"
  }
 },
 {
  "id": "mistral_ai",
  "name": "MistralAI",
  "python": {
   "code": "from traceai_mistralai import MistralAIInstrumentor\n\nMistralAIInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-mistralai"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/mistralai"
  }
 },
 {
  "id": "vertex_ai",
  "name": "VertexAI",
  "python": {
   "code": "from traceai_vertexai import VertexAIInstrumentor\n\nVertexAIInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-vertexai"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/vertexai"
  }
 },
 {
  "id": "google_adk",
  "name": "Google ADK",
  "python": {
   "code": "from traceai_google_adk import GoogleADKInstrumentor\n\nGoogleADKInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-google-adk"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/google-adk"
  }
 },
 {
  "id": "google_genai",
  "name": "Google GenAI",
  "python": {
   "code": "from traceai_google_genai import GoogleGenAIInstrumentor\n\nGoogleGenAIInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-google-genai"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/google-genai"
  }
 },
 {
  "id": "together_ai",
  "name": "TogetherAI",
  "python": {
   "code": "from traceai_openai import OpenAIInstrumentor\n\nOpenAIInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-openai"
   ],
   "github": ""
  }
 },
 {
  "id": "openai_agents",
  "name": "OpenAIAgents",
  "python": {
   "code": "from traceai_openai_agents import OpenAIAgentsInstrumentor\n\nOpenAIAgentsInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-openai-agents"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/openai-agents"
  }
 },
 {
  "id": "autogen",
  "name": "Autogen",
  "python": {
   "code": "from traceai_autogen import AutogenInstrumentor\n\nAutogenInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-autogen"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/autogen"
  }
 },
 {
  "id": "guardrails",
  "name": "Guardrails",
  "python": {
   "code": "from traceai_guardrails import GuardrailsInstrumentor\n\nGuardrailsInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-guardrails"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/guardrails"
  }
 },
 {
  "id": "lang_graph",
  "name": "LangGraph",
  "python": {
   "code": "from traceai_langchain import LangChainInstrumentor\n\nLangChainInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-langchain"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/langchain"
  }
 },
 {
  "id": "smol_agents",
  "name": "SmolAgents",
  "python": {
   "code": "from traceai_smolagents import SmolagentsInstrumentor\n\nSmolagentsInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-smolagents"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/smolagents"
  }
 },
 {
  "id": "ollama",
  "name": "Ollama",
  "python": {
   "code": "from traceai_openai import OpenAIInstrumentor\n\nOpenAIInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-openai"
   ],
   "github": ""
  }
 },
 {
  "id": "prompt_flow",
  "name": "PromptFlow",
  "python": {
   "code": "from traceai_openai import OpenAIInstrumentor\n\nOpenAIInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-openai"
   ],
   "github": ""
  }
 },
 {
  "id": "portkey",
  "name": "Portkey",
  "python": {
   "code": "from traceai_portkey import PortkeyInstrumentor\n\nPortkeyInstrumentor().instrument(tracer_provider=trace_provider)",
   "pkgs": [
    "traceAI-portkey"
   ],
   "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/portkey"
  }
 }
];

const byId = (id) => TRACE_AI_INSTRUMENTORS.find((i) => i.id === id) || null;
export const traceAiInstrumentor = byId;

/* The instrumentor for each framework the connect flows know, by framework id. */
const FOR_FRAMEWORK = {
  langgraph: "lang_graph",
  "openai-agents": "openai_agents",
  crewai: "crewai",
  "claude-agent-sdk": "anthropic",
  "google-adk": "google_adk",
  haystack: "haystack",
  llamaindex: "llama_index",
  smolagents: "smol_agents",
  dspy: "dspy",
};

/**
 * Which instrumentor fits an agent: its framework's own, else its model
 * provider's — traceAI has no instrumentor for every framework, and tracing
 * the model calls still shows the prompt, model and tools.
 *   → { id, fallback } — `fallback` names the framework that has none.
 */
export function instrumentorFor({ frameworkId, frameworkLabel, model } = {}) {
  if (frameworkId && FOR_FRAMEWORK[frameworkId]) return { id: FOR_FRAMEWORK[frameworkId], fallback: null };
  const m = String(model || "").toLowerCase();
  const id = /claude/.test(m) ? "anthropic" : /gemini/.test(m) ? "google_genai" : /mistral/.test(m) ? "mistral_ai" : "openai";
  return { id, fallback: frameworkId ? frameworkLabel || frameworkId : null };
}

/**
 * The four things Observe's New project drawer asks for, for one project and
 * instrumentor: install, keys, register, instrument. Keys are set where the
 * agent runs — never written into the code. TypeScript only where the
 * instrumentor has it (`ts: false` falls back to Python).
 */
export function traceAiSnippets({ instrumentorId = "openai", language = "python", project = "my-agent" } = {}) {
  const ins = byId(instrumentorId) || byId("openai");
  const ts = language === "typescript" && !!ins.typescript;
  if (ts) {
    const pkgs = [...new Set((ins.typescript.code.match(/from "(@[^"]+)"/g) || []).map((s) => s.slice(6, -1)))];
    return {
      ts: true,
      install: `${TRACE_AI_INSTALL.typescript} ${pkgs.join(" ")}`,
      keys: 'export FI_API_KEY="your-api-key"\nexport FI_SECRET_KEY="your-secret-key"',
      register: [
        'import { register, ProjectType } from "@traceai/fi-core";',
        "",
        "const tracerProvider = register({",
        "  projectType: ProjectType.OBSERVE,",
        `  projectName: "${project}",`,
        "});",
      ].join("\n"),
      instrument: ins.typescript.code,
    };
  }
  return {
    ts: false,
    install: `${TRACE_AI_INSTALL.python} ${(ins.python?.pkgs || []).join(" ")}`.trim(),
    keys: 'export FI_API_KEY="your-api-key"\nexport FI_SECRET_KEY="your-secret-key"',
    register: [
      "from fi_instrumentation import register",
      "from fi_instrumentation.fi_types import ProjectType",
      "",
      "trace_provider = register(",
      "    project_type=ProjectType.OBSERVE,",
      `    project_name="${project}",`,
      ")",
    ].join("\n"),
    instrument: ins.python?.code || "",
  };
}

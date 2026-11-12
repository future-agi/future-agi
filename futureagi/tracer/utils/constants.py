from enum import Enum

from tracer.utils.filter_operators import (
    LIST_FILTER_OPS,
    NO_VALUE_FILTER_OPS,
    RANGE_FILTER_OPS,
)
from tracer.utils.filter_operators import (
    SPAN_ATTR_ALLOWED_OPS as CONTRACT_SPAN_ATTR_ALLOWED_OPS,
)

INSTALLATION_GUIDE = {
    "Python": """
pip install fi-instrumentation-otel
""",
    "TypeScript": """
npm install @traceai/fi-core
""",
}

PROTOTYPE_CODEBLOCK = {
    "Python": """
from fi_instrumentation import register
from fi_instrumentation.fi_types import ProjectType

# Setup OTel via our register function
trace_provider = register(
    project_type=ProjectType.EXPERIMENT,
    project_name="FUTURE_AGI",            # Your project name
    project_version_name="openai-exp",    # Version identifier for this prototype
)
""",
    "TypeScript": """
import { register, ProjectType } from "@traceai/fi-core";

const tracerProvider = register({
    projectType: ProjectType.EXPERIMENT,
    projectName: "FUTURE_AGI",
    projectVersionName: "openai-exp",
});
""",
}

OBSERVE_CODEBLOCK = {
    "Python": """
from fi_instrumentation import register
from fi_instrumentation.fi_types import ProjectType

# Setup OTel via our register function
trace_provider = register(
    project_type=ProjectType.OBSERVE,
    project_name="my-project",            # Your project name
)
""",
    "TypeScript": """
import { register, ProjectType } from "@traceai/fi-core";

const tracerProvider = register({
    projectType: ProjectType.OBSERVE,
    projectName: "my-project",
});
""",
}

ORG_KEYS = {
    "Python": """
import os

os.environ["FI_API_KEY"] = "{}"
os.environ["FI_SECRET_KEY"] = "{}"
""",
    "TypeScript": """
process.env.FI_API_KEY = "{}";
process.env.FI_SECRET_KEY = "{}";
""",
}

# Appended to ORG_KEYS off Future AGI Cloud (tracer.views.project): both SDKs
# default FI_BASE_URL to Cloud, so a self-hosted snippet points them at the
# install's own collector.
ORG_BASE_URL = {
    "Python": 'os.environ["FI_BASE_URL"] = "{}"\n',
    "TypeScript": 'process.env.FI_BASE_URL = "{}";\n',
}

INSTRUMENTORS = {
    "langchain": {
        "name": "LangChain",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/d6cc0d59-d1a7-4c78-8793-78a2249f7191",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/langchain",
            "code": """# pip install traceai-langchain
from traceai_langchain import LangChainInstrumentor

LangChainInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_langchain",
            "code": """// npm install @traceai/langchain @langchain/core@^1 @opentelemetry/instrumentation@^0.202.0
import { LangChainInstrumentation } from "@traceai/langchain";
import { registerInstrumentations } from "@opentelemetry/instrumentation";
import * as CallbackManagerModule from "@langchain/core/callbacks/manager";
import * as ChatModelModule from "@langchain/core/language_models/chat_models";
import * as RunnableModule from "@langchain/core/runnables";

const langchainInstrumentation = new LangChainInstrumentation();

registerInstrumentations({
  instrumentations: [langchainInstrumentation],
  tracerProvider: tracerProvider,
});

// LangChain.js is instrumented manually: pass all three modules.
langchainInstrumentation.manuallyInstrument(
  CallbackManagerModule,
  ChatModelModule,
  RunnableModule,
);
""",
        },
    },
    "openai": {
        "name": "OpenAI",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/74883bc5-fa3e-4199-be02-140ac4217967",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/openai",
            "code": """# pip install traceai-openai
from traceai_openai import OpenAIInstrumentor

OpenAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_openai",
            "code": """// npm install @traceai/openai @opentelemetry/instrumentation
import { OpenAIInstrumentation } from "@traceai/openai";
import { registerInstrumentations } from "@opentelemetry/instrumentation";

const openaiInstrumentation = new OpenAIInstrumentation({});

  registerInstrumentations({
    instrumentations: [openaiInstrumentation],
    tracerProvider: tracerProvider,
  });
""",
        },
    },
    "anthropic": {
        "name": "Anthropic",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/7d09d9b3-132f-47d2-a11f-e066faa9fd28",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/anthropic",
            "code": """# pip install traceai-anthropic
from traceai_anthropic import AnthropicInstrumentor

AnthropicInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_anthropic",
            "code": """// npm install @traceai/anthropic @opentelemetry/instrumentation
import { AnthropicInstrumentation } from "@traceai/anthropic";
import { registerInstrumentations } from "@opentelemetry/instrumentation";

 const anthropicInstrumentation = new AnthropicInstrumentation({});

  registerInstrumentations({
    instrumentations: [anthropicInstrumentation],
    tracerProvider: tracerProvider,
  });
""",
        },
    },
    "mcp": {
        "name": "MCP (Model Context Protocol)",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/892e8cfe-527c-4f41-a659-bfeac97d987c/441ead79-6491-47e6-ad53-5bd1f14198c0",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/mcp",
            "code": """# pip install traceai-openai-agents traceai-mcp
from traceai_openai_agents import OpenAIAgentsInstrumentor
from traceai_mcp import MCPInstrumentor


OpenAIAgentsInstrumentor().instrument(tracer_provider=trace_provider)
MCPInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_mcp",
            "code": """// npm install @traceai/mcp @modelcontextprotocol/sdk
import { MCPInstrumentation } from "@traceai/mcp";
import * as MCPClientStdioModule from "@modelcontextprotocol/sdk/client/stdio.js";
import * as MCPServerStdioModule from "@modelcontextprotocol/sdk/server/stdio.js";

const mcpInstrumentation = new MCPInstrumentation();

// MCP must be manually instrumented as it doesn't have a traditional module structure
mcpInstrumentation.manuallyInstrument({
  clientStdioModule: MCPClientStdioModule,
  serverStdioModule: MCPServerStdioModule,
});
""",
        },
    },
    "bedrock": {
        "name": "Bedrock",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/5839cb7b-a5cd-450e-b16d-d28f8943fb40",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/bedrock",
            "code": """# pip install traceai-bedrock
from traceai_bedrock import BedrockInstrumentor

BedrockInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_bedrock",
            "code": """// npm install @traceai/bedrock @opentelemetry/instrumentation
import { BedrockInstrumentation } from "@traceai/bedrock";
import { registerInstrumentations } from "@opentelemetry/instrumentation";

const bedrockInstrumentation = new BedrockInstrumentation();

registerInstrumentations({
  instrumentations: [bedrockInstrumentation],
  tracerProvider: tracerProvider,
});
""",
        },
    },
    "crewai": {
        "name": "CrewAI",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/e8148d8d-1315-47d1-9cde-9e3791af01c2",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/crewai",
            "code": """# pip install traceai-crewai
from traceai_crewai import CrewAIInstrumentor

CrewAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "dspy": {
        "name": "DSPy",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/1f5a9826-5e0a-4ea0-8d68-d03921800c76",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/dspy",
            "code": """# pip install traceai-dspy
from traceai_dspy import DSPyInstrumentor

DSPyInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "groq": {
        "name": "Groq",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/bfcfe710-1354-4f22-b31a-4af6451877fb",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/groq",
            "code": """# pip install traceai-groq
from traceai_groq import GroqInstrumentor

GroqInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_groq",
            "code": """// npm install @traceai/groq groq-sdk
import { GroqInstrumentation } from "@traceai/groq";
import Groq from "groq-sdk";

const groqInstrumentation = new GroqInstrumentation();
groqInstrumentation.setTracerProvider(tracerProvider);
groqInstrumentation.manuallyInstrument({ Groq });
""",
        },
    },
    "haystack": {
        "name": "Haystack",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/e1a001da-e41d-4114-b6c6-1e4588fc366b",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/haystack",
            "code": """# pip install traceai-haystack
from traceai_haystack import HaystackInstrumentor

HaystackInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "instructor": {
        "name": "Instructor",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/169d90e3-7ea8-425e-b4c0-dd2a2378e6a0",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/instructor",
            "code": """# pip install traceai-instructor
from traceai_instructor import InstructorInstrumentor

InstructorInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_instructor",
            "code": """// npm install @traceai/instructor @opentelemetry/instrumentation@^0.202.0
import { InstructorInstrumentation } from "@traceai/instructor";
import { registerInstrumentations } from "@opentelemetry/instrumentation";

const instructorInstrumentation = new InstructorInstrumentation();

registerInstrumentations({
  instrumentations: [instructorInstrumentation],
  tracerProvider: tracerProvider,
});
""",
        },
    },
    "litellm": {
        "name": "LiteLLM",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/ba1bc85d-2bc7-4a6e-9a32-fead0099473e",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/litellm",
            "code": """# pip install traceai-litellm
from traceai_litellm import LiteLLMInstrumentor

LiteLLMInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "llama_index": {
        "name": "LlamaIndex",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/4ca29971-7e09-44e2-9656-06a94ba940c2",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/llama_index",
            "code": """# pip install traceai-llamaindex
from traceai_llamaindex import LlamaIndexInstrumentor

LlamaIndexInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "mistral_ai": {
        "name": "MistralAI",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/f06a6a80-e785-423c-8acb-51c68b0f37cc",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/mistralai",
            "code": """# pip install traceai-mistralai
from traceai_mistralai import MistralAIInstrumentor

MistralAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_mistral",
            "code": """// npm install @traceai/mistral @mistralai/mistralai
import { MistralInstrumentation } from "@traceai/mistral";
import { Mistral } from "@mistralai/mistralai";

const mistralInstrumentation = new MistralInstrumentation();
mistralInstrumentation.setTracerProvider(tracerProvider);

const client = mistralInstrumentation.instrumentClient(
  new Mistral({ apiKey: process.env.MISTRAL_API_KEY }),
);
""",
        },
    },
    "vertex_ai": {
        "name": "VertexAI",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/92951ea2-5a50-462b-ab95-6b5c3c9ce89b",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/vertexai",
            "code": """# pip install traceai-vertexai
from traceai_vertexai import VertexAIInstrumentor

VertexAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_vertexai",
            "code": """// npm install @traceai/fi-instrumentation-vertexai @opentelemetry/instrumentation
import { VertexAIInstrumentation } from "@traceai/fi-instrumentation-vertexai";
import { registerInstrumentations } from "@opentelemetry/instrumentation";

const vertexAIInstrumentation = new VertexAIInstrumentation();

registerInstrumentations({
  instrumentations: [vertexAIInstrumentation],
  tracerProvider: tracerProvider,
});
""",
        },
    },
    "google_adk": {
        "name": "Google ADK",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/adk.png",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/google-adk",
            "code": """# pip install traceai-google-adk
from traceai_google_adk import GoogleADKInstrumentor

GoogleADKInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "google_genai": {
        "name": "Google GenAI",
        "logo": "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/9fed909e-b0ed-40f6-b9df-f9dece3110a0/441cb6be-b1ff-453a-9a14-22dc35f9b9a2",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/google-genai",
            "code": """# pip install traceai-google-genai
from traceai_google_genai import GoogleGenAIInstrumentor

GoogleGenAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_google_genai_sdk",
            "code": """// npm install @traceai/google-genai-sdk @google/genai@^2.32.0
import { instrumentGoogleGenAI } from "@traceai/google-genai-sdk";
import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({ apiKey: process.env.GOOGLE_API_KEY });
instrumentGoogleGenAI(ai, { tracerProvider: tracerProvider });
""",
        },
    },
    "together_ai": {
        "name": "TogetherAI",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/e5cabf77-de9b-44ec-a50c-1553d0f14039",
        "Python": {
            "github": "",
            "code": """# pip install traceai-openai
from traceai_openai import OpenAIInstrumentor

OpenAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "openai_agents": {
        "name": "OpenAIAgents",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/74883bc5-fa3e-4199-be02-140ac4217967",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/openai-agents",
            "code": """# pip install traceai-openai-agents
from traceai_openai_agents import OpenAIAgentsInstrumentor

OpenAIAgentsInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_openai_agents",
            "code": """// npm install @traceai/openai-agents @openai/agents
import { OpenAIAgentsInstrumentation } from "@traceai/openai-agents";
import * as agents from "@openai/agents";

const agentsInstrumentation = new OpenAIAgentsInstrumentation();
agentsInstrumentation.setTracerProvider(tracerProvider);
agentsInstrumentation.manuallyInstrument(agents);
""",
        },
    },
    "autogen": {
        "name": "Autogen",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/2536d4e9-d89c-4935-8d3c-d7150ead2c47",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/autogen",
            "code": """# pip install traceai-autogen
from traceai_autogen import AutogenInstrumentor

AutogenInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "guardrails": {
        "name": "Guardrails",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/961eec1e-8b82-4c33-beb0-14ebb35a14b3",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/guardrails",
            "code": """# pip install traceai-guardrails
from traceai_guardrails import GuardrailsInstrumentor

GuardrailsInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_guardrails",
            "code": """// npm install @traceai/guardrails @guardrails-ai/core@0.1.1
// @guardrails-ai/core 0.1.x runs Python guardrails-ai==0.4.2; keep that Python environment active.
import { GuardrailsInstrumentation } from "@traceai/guardrails";
import * as guardrails from "@guardrails-ai/core";

const guardrailsInstrumentation = new GuardrailsInstrumentation();
guardrailsInstrumentation.setTracerProvider(tracerProvider);
guardrailsInstrumentation.manuallyInstrument(guardrails);
""",
        },
    },
    "lang_graph": {
        "name": "LangGraph",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/4357eb1b-6f4a-4672-a9b2-15eafc22d409",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/langchain",
            "code": """# pip install traceai-langchain
from traceai_langchain import LangChainInstrumentor

LangChainInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_langchain",
            "code": """// npm install @traceai/langchain @langchain/core@^1 @opentelemetry/instrumentation@^0.202.0
import { LangChainInstrumentation } from "@traceai/langchain";
import { registerInstrumentations } from "@opentelemetry/instrumentation";
import * as CallbackManagerModule from "@langchain/core/callbacks/manager";
import * as ChatModelModule from "@langchain/core/language_models/chat_models";
import * as RunnableModule from "@langchain/core/runnables";

const langchainInstrumentation = new LangChainInstrumentation();

registerInstrumentations({
  instrumentations: [langchainInstrumentation],
  tracerProvider: tracerProvider,
});

// LangChain.js is instrumented manually: pass all three modules.
langchainInstrumentation.manuallyInstrument(
  CallbackManagerModule,
  ChatModelModule,
  RunnableModule,
);
""",
        },
    },
    "smol_agents": {
        "name": "SmolAgents",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/b83520f0-90d4-4e57-944a-0e9d5bec23c0",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/smolagents",
            "code": """# pip install traceai-smolagents
from traceai_smolagents import SmolagentsInstrumentor

SmolagentsInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "ollama": {
        "name": "Ollama",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/167869d4-fb55-4b21-ae55-25193d18f012",
        "Python": {
            "github": "",
            "code": """# pip install traceai-openai
from traceai_openai import OpenAIInstrumentor

OpenAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_ollama",
            "code": """// npm install @traceai/ollama ollama @opentelemetry/instrumentation
import { OllamaInstrumentation } from "@traceai/ollama";
import { registerInstrumentations } from "@opentelemetry/instrumentation";
import * as OllamaModule from "ollama";

const ollamaInstrumentation = new OllamaInstrumentation();

registerInstrumentations({
  instrumentations: [ollamaInstrumentation],
  tracerProvider: tracerProvider,
});

// Patch the imported namespace explicitly for native ESM
ollamaInstrumentation.manuallyInstrument(OllamaModule);
""",
        },
    },
    "prompt_flow": {
        "name": "PromptFlow",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/5847c5ac-e023-4eea-9583-8b4089368431",
        "Python": {
            "github": "",
            "code": """# pip install traceai-openai
from traceai_openai import OpenAIInstrumentor

OpenAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "portkey": {
        "name": "Portkey",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/892e8cfe-527c-4f41-a659-bfeac97d987c/24872ef1-a439-4584-95b7-9aaca7f40873",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/portkey",
            "code": """# pip install traceai-portkey
from traceai_portkey import PortkeyInstrumentor

PortkeyInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_portkey",
            "code": """// npm install @traceai/portkey portkey-ai
import { PortkeyInstrumentation } from "@traceai/portkey";
import { Portkey } from "portkey-ai";

const portkeyInstrumentation = new PortkeyInstrumentation();
portkeyInstrumentation.setTracerProvider(tracerProvider);

const client = new Portkey({
  apiKey: process.env.PORTKEY_API_KEY,
  virtualKey: process.env.PORTKEY_VIRTUAL_KEY,
});
portkeyInstrumentation.manuallyInstrument(client);
""",
        },
    },
    "vercel": {
        "name": "Vercel AI SDK",
        "logo": "",
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_vercel",
            "code": """// npm install @traceai/vercel @opentelemetry/instrumentation
import { VercelAIInstrumentation } from "@traceai/vercel";
import { registerInstrumentations } from "@opentelemetry/instrumentation";

const vercelAIInstrumentation = new VercelAIInstrumentation();

registerInstrumentations({
  instrumentations: [vercelAIInstrumentation],
  tracerProvider: tracerProvider,
});
""",
        },
    },
}


class FilterType(Enum):
    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"


# SPAN_ATTRIBUTE filter vocabulary shared by the CH builder and the Django ORM
# validator. Single source of truth for allowed filter ops per type.
SPAN_ATTR_ALLOWED_OPS: dict[str, set[str]] = CONTRACT_SPAN_ATTR_ALLOWED_OPS
LIST_OPS: set[str] = LIST_FILTER_OPS
RANGE_OPS: set[str] = RANGE_FILTER_OPS
NO_VALUE_OPS: set[str] = NO_VALUE_FILTER_OPS

from tracer.utils.filter_operators import (
    LIST_FILTER_OPS,
    NO_VALUE_FILTER_OPS,
    RANGE_FILTER_OPS,
    SPAN_ATTR_ALLOWED_OPS as CONTRACT_SPAN_ATTR_ALLOWED_OPS,
)

INSTALLATION_GUIDE = {
    "Python": """
pip install fi-instrumentation-otel
""",
    "TypeScript": """
npm install @traceai/openai
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
    project_type: ProjectType.EXPERIMENT,
    project_name: "FUTURE_AGI",
    project_version_name: "openai-exp",
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
    project_name="FUTURE_AGI",            # Your project name
    session_name="chat-bot"               # Session name
)
""",
    "TypeScript": """
import { register, ProjectType } from "@traceai/fi-core";

const tracerProvider = register({
    project_type: ProjectType.OBSERVE,
    project_name: "openai_project",
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

INSTRUMENTORS = {
    "langchain": {
        "name": "LangChain",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/d6cc0d59-d1a7-4c78-8793-78a2249f7191",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/langchain",
            "code": """from traceai_langchain import LangChainInstrumentor

LangChainInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "openai": {
        "name": "OpenAI",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/74883bc5-fa3e-4199-be02-140ac4217967",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/openai",
            "code": """from traceai_openai import OpenAIInstrumentor

OpenAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_openai",
            "code": """import { OpenAIInstrumentation } from "@traceai/openai";
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
            "code": """from traceai_anthropic import AnthropicInstrumentor

AnthropicInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
        "TypeScript": {
            "github": "https://github.com/future-agi/traceAI/tree/main/typescript/packages/traceai_anthropic",
            "code": """
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
            "code": """from traceai_openai_agents import OpenAIAgentsInstrumentor
from traceai_mcp import MCPInstrumentor


OpenAIAgentsInstrumentor().instrument(tracer_provider=trace_provider)
MCPInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "bedrock": {
        "name": "Bedrock",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/5839cb7b-a5cd-450e-b16d-d28f8943fb40",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/bedrock",
            "code": """from traceai_bedrock import BedrockInstrumentor

BedrockInstrumentor().instrument(tracer_provider=trace_provider))
""",
        },
    },
    "crewai": {
        "name": "CrewAI",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/e8148d8d-1315-47d1-9cde-9e3791af01c2",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/crewai",
            "code": """from traceai_crewai import CrewAIInstrumentor

CrewAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "dspy": {
        "name": "DSPy",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/1f5a9826-5e0a-4ea0-8d68-d03921800c76",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/dspy",
            "code": """from traceai_dspy import DSPyInstrumentor

DSPyInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "groq": {
        "name": "Groq",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/bfcfe710-1354-4f22-b31a-4af6451877fb",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/groq",
            "code": """from traceai_groq import GroqInstrumentor

GroqInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "haystack": {
        "name": "Haystack",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/e1a001da-e41d-4114-b6c6-1e4588fc366b",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/haystack",
            "code": """from traceai_haystack import HaystackInstrumentor

HaystackInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "instructor": {
        "name": "Instructor",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/169d90e3-7ea8-425e-b4c0-dd2a2378e6a0",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/instructor",
            "code": """from traceai_instructor import InstructorInstrumentor

InstructorInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "litellm": {
        "name": "LiteLLM",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/ba1bc85d-2bc7-4a6e-9a32-fead0099473e",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/litellm",
            "code": """from traceai_litellm import LiteLLMInstrumentor

LiteLLMInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "llama_index": {
        "name": "LlamaIndex",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/4ca29971-7e09-44e2-9656-06a94ba940c2",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/llama_index",
            "code": """from traceai_llamaindex import LlamaIndexInstrumentor

LlamaIndexInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "mistral_ai": {
        "name": "MistralAI",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/f06a6a80-e785-423c-8acb-51c68b0f37cc",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/mistralai",
            "code": """from traceai_mistralai import MistralAIInstrumentor

MistralAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "vertex_ai": {
        "name": "VertexAI",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/92951ea2-5a50-462b-ab95-6b5c3c9ce89b",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/vertexai",
            "code": """from traceai_vertexai import VertexAIInstrumentor

VertexAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "google_adk": {
        "name": "Google ADK",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/adk.png",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/google-adk",
            "code": """from traceai_google_adk import GoogleADKInstrumentor

GoogleADKInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "google_genai": {
        "name": "Google GenAI",
        "logo": "https://fi-content-dev.s3.ap-south-1.amazonaws.com/images/9fed909e-b0ed-40f6-b9df-f9dece3110a0/441cb6be-b1ff-453a-9a14-22dc35f9b9a2",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/google-genai",
            "code": """from traceai_google_genai import GoogleGenAIInstrumentor

GoogleGenAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "together_ai": {
        "name": "TogetherAI",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/e5cabf77-de9b-44ec-a50c-1553d0f14039",
        "Python": {
            "github": "",
            "code": """from traceai_openai import OpenAIInstrumentor

OpenAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "openai_agents": {
        "name": "OpenAIAgents",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/74883bc5-fa3e-4199-be02-140ac4217967",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/openai-agents",
            "code": """from traceai_openai_agents import OpenAIAgentsInstrumentor

OpenAIAgentsInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "autogen": {
        "name": "Autogen",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/2536d4e9-d89c-4935-8d3c-d7150ead2c47",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/autogen",
            "code": """from traceai_autogen import AutogenInstrumentor

AutogenInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "guardrails": {
        "name": "Guardrails",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/961eec1e-8b82-4c33-beb0-14ebb35a14b3",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/guardrails",
            "code": """from traceai_guardrails import GuardrailsInstrumentor

GuardrailsInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "lang_graph": {
        "name": "LangGraph",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/4357eb1b-6f4a-4672-a9b2-15eafc22d409",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/langchain",
            "code": """from traceai_langchain import LangChainInstrumentor

LangChainInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "smol_agents": {
        "name": "SmolAgents",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/b83520f0-90d4-4e57-944a-0e9d5bec23c0",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/smolagents",
            "code": """from traceai_smolagents import SmolagentsInstrumentor

SmolagentsInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "ollama": {
        "name": "Ollama",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/167869d4-fb55-4b21-ae55-25193d18f012",
        "Python": {
            "github": "",
            "code": """from traceai_openai import OpenAIInstrumentor

OpenAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "prompt_flow": {
        "name": "PromptFlow",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/1846a0f6-6030-40a0-8080-402052ffd734/5847c5ac-e023-4eea-9583-8b4089368431",
        "Python": {
            "github": "",
            "code": """from traceai_openai import OpenAIInstrumentor

OpenAIInstrumentor().instrument(tracer_provider=trace_provider)
""",
        },
    },
    "portkey": {
        "name": "Portkey",
        "logo": "https://fi-content.s3.ap-south-1.amazonaws.com/images/892e8cfe-527c-4f41-a659-bfeac97d987c/24872ef1-a439-4584-95b7-9aaca7f40873",
        "Python": {
            "github": "https://github.com/future-agi/traceAI/tree/main/python/frameworks/portkey",
            "code": """from traceai_portkey import PortkeyInstrumentor

PortkeyInstrumentor().instrument(tracer_provider=tracer_provider)
""",
        },
    },
}


# SPAN_ATTRIBUTE filter vocabulary shared by the CH builder and the Django ORM
# validator. Single source of truth for allowed filter ops per type.
SPAN_ATTR_ALLOWED_OPS: dict[str, set[str]] = CONTRACT_SPAN_ATTR_ALLOWED_OPS
LIST_OPS: set[str] = LIST_FILTER_OPS
RANGE_OPS: set[str] = RANGE_FILTER_OPS
NO_VALUE_OPS: set[str] = NO_VALUE_FILTER_OPS

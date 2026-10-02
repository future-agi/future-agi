import { describe, it, expect } from "vitest";
import {
  normalizeMessagesFromSpan,
  extractModelFromSpan,
  extractProviderFromSpan,
  extractParamsFromSpan,
  extractResponseFormatFromSpan,
  buildPromptConfigFromSpan,
} from "../promptFromSpan";

describe("normalizeMessagesFromSpan", () => {
  // Mastra/Gemini: gen_ai.input.messages stored as an ARRAY value
  it("parses an array-valued gen_ai.input.messages (parts[].content)", () => {
    const span = {
      span_attributes: {
        "gen_ai.input.messages": [
          {
            role: "system",
            parts: [{ content: "You are helpful", type: "text" }],
          },
          {
            role: "user",
            parts: [{ content: "Weather in Mumbai?", type: "text" }],
          },
        ],
      },
    };
    expect(normalizeMessagesFromSpan(span)).toEqual([
      { role: "system", content: "You are helpful" },
      { role: "user", content: "Weather in Mumbai?" },
    ]);
  });

  // Mastra: object input {messages:[{role, content: str | [{type,text}]}]}
  it("parses {messages:[...]} object input with string/array content", () => {
    const span = {
      input: {
        messages: [
          { role: "system", content: "You are helpful" },
          { role: "user", content: [{ type: "text", text: "Weather?" }] },
        ],
      },
    };
    expect(normalizeMessagesFromSpan(span)).toEqual([
      { role: "system", content: "You are helpful" },
      { role: "user", content: "Weather?" },
    ]);
  });

  // Google ADK: nested message.contents.N.message_content.text (+ .type)
  it("parses nested message_content.text and ignores the .type sibling", () => {
    const span = {
      span_attributes: {
        "gen_ai.input.messages.0.message.role": "system",
        "gen_ai.input.messages.0.message.content": "Delegate weather Qs",
        "gen_ai.input.messages.1.message.role": "user",
        "gen_ai.input.messages.1.message.contents.0.message_content.text":
          "What's the weather in NY?",
        "gen_ai.input.messages.1.message.contents.0.message_content.type":
          "text",
      },
    };
    expect(normalizeMessagesFromSpan(span)).toEqual([
      { role: "system", content: "Delegate weather Qs" },
      { role: "user", content: "What's the weather in NY?" },
    ]);
  });

  // LangChain: flattened, system NOT first, object content, multi-turn
  it("parses flattened multi-turn with system out of order and object content", () => {
    const span = {
      span_attributes: {
        "gen_ai.input.messages.0.message.role": "user",
        "gen_ai.input.messages.0.message.content": "Hello",
        "gen_ai.input.messages.1.message.role": "system",
        "gen_ai.input.messages.1.message.content": "You are an assistant",
        "gen_ai.input.messages.2.message.role": "assistant",
        "gen_ai.input.messages.2.message.content": { response: "Hi there" },
      },
    };
    expect(normalizeMessagesFromSpan(span)).toEqual([
      { role: "user", content: "Hello" },
      { role: "system", content: "You are an assistant" },
      { role: "assistant", content: '{"response":"Hi there"}' },
    ]);
  });

  // Simple OpenInference (the case that already worked) — canonical snake keys
  it("parses canonical llm.input_messages flat strings", () => {
    const span = {
      span_attributes: {
        "llm.input_messages.0.message.role": "system",
        "llm.input_messages.0.message.content": "You are helpful",
        "llm.input_messages.1.message.role": "user",
        "llm.input_messages.1.message.content": "Hi",
      },
    };
    expect(normalizeMessagesFromSpan(span)).toEqual([
      { role: "system", content: "You are helpful" },
      { role: "user", content: "Hi" },
    ]);
  });

  it("returns [] for an unparseable object input (no raw-blob dump)", () => {
    const span = {
      span_attributes: {},
      input: { config: { http_options: { headers: {} } } },
    };
    expect(normalizeMessagesFromSpan(span)).toEqual([]);
  });

  // Bug :189 regression — a STRINGIFIED config object (no messages) must not be
  // dumped raw into a user turn the way real prose is.
  it("returns [] for a stringified config object input (no raw-blob dump)", () => {
    const span = {
      span_attributes: {},
      input: JSON.stringify({ config: { http_options: { headers: {} } } }),
    };
    expect(normalizeMessagesFromSpan(span)).toEqual([]);
  });

  it("treats a plain-text input as a single user message", () => {
    expect(normalizeMessagesFromSpan({ input: "Hello there" })).toEqual([
      { role: "user", content: "Hello there" },
    ]);
  });

  // Bug :91 — { message: { role, content } } wrapper must read nested content,
  // not yield a role with blank content.
  it("reads nested content from a { message: {...} } wrapper array", () => {
    const span = {
      span_attributes: {
        "gen_ai.input.messages": [
          { message: { role: "system", content: "You are helpful" } },
          { message: { role: "user", content: "Hi" } },
        ],
      },
    };
    expect(normalizeMessagesFromSpan(span)).toEqual([
      { role: "system", content: "You are helpful" },
      { role: "user", content: "Hi" },
    ]);
  });

  // Bug :147 — gen_ai.input.messages recorded as a JSON STRING (common OTel attr)
  // must be parsed, not skipped.
  it("parses gen_ai.input.messages stored as a JSON string", () => {
    const span = {
      span_attributes: {
        "gen_ai.input.messages": JSON.stringify([
          { role: "system", content: "You are helpful" },
          { role: "user", content: "Hi" },
        ]),
      },
    };
    expect(normalizeMessagesFromSpan(span)).toEqual([
      { role: "system", content: "You are helpful" },
      { role: "user", content: "Hi" },
    ]);
  });

  // OpenAI legacy Completions: { prompt: "..." }
  it("parses a { prompt } completions input as one user message", () => {
    expect(normalizeMessagesFromSpan({ input: { prompt: "Say hi" } })).toEqual([
      { role: "user", content: "Say hi" },
    ]);
  });

  // OpenAI Responses API: { input: string } and { input: [...] }
  it("parses a { input: string } Responses input as one user message", () => {
    expect(normalizeMessagesFromSpan({ input: { input: "Hello" } })).toEqual([
      { role: "user", content: "Hello" },
    ]);
  });

  it("parses a { input: [...] } Responses message array", () => {
    const span = {
      input: {
        input: [
          { role: "system", content: "You are helpful" },
          { role: "user", content: "Hi" },
        ],
      },
    };
    expect(normalizeMessagesFromSpan(span)).toEqual([
      { role: "system", content: "You are helpful" },
      { role: "user", content: "Hi" },
    ]);
  });

  // OpenAI Responses keeps the system prompt in a sibling `instructions` field.
  it("prepends OpenAI Responses `instructions` as the system message", () => {
    const span = {
      input: {
        instructions: "You are helpful",
        input: [{ role: "user", content: "Hi" }],
      },
    };
    expect(normalizeMessagesFromSpan(span)).toEqual([
      { role: "system", content: "You are helpful" },
      { role: "user", content: "Hi" },
    ]);
  });

  // Cohere: { chat_history: [{role, message}], message: "..." } — roles normalized
  // (USER→user, CHATBOT→assistant).
  it("parses Cohere { chat_history, message } with normalized roles", () => {
    const span = {
      input: {
        chat_history: [
          { role: "USER", message: "Hi" },
          { role: "CHATBOT", message: "Hello" },
        ],
        message: "What's next?",
      },
    };
    expect(normalizeMessagesFromSpan(span)).toEqual([
      { role: "user", content: "Hi" },
      { role: "assistant", content: "Hello" },
      { role: "user", content: "What's next?" },
    ]);
  });

  // Cohere keeps the system prompt in a sibling `preamble` field.
  it("prepends Cohere `preamble` as the system message", () => {
    const span = {
      input: {
        preamble: "You are helpful",
        chat_history: [{ role: "USER", message: "Hi" }],
        message: "What's next?",
      },
    };
    expect(normalizeMessagesFromSpan(span)).toEqual([
      { role: "system", content: "You are helpful" },
      { role: "user", content: "Hi" },
      { role: "user", content: "What's next?" },
    ]);
  });

  // Gemini uses role "model" for the assistant turn — normalized to "assistant".
  it("normalizes the Gemini 'model' role to 'assistant'", () => {
    const span = {
      input: {
        contents: [
          { role: "user", parts: [{ text: "Hi" }] },
          { role: "model", parts: [{ text: "Hello" }] },
        ],
      },
    };
    expect(normalizeMessagesFromSpan(span)).toEqual([
      { role: "user", content: "Hi" },
      { role: "assistant", content: "Hello" },
    ]);
  });

  // Gemini: { contents: [...], config: { system_instruction } } — system prompt
  // is nested in config and must be prepended, not dropped.
  it("parses Gemini contents and prepends config.system_instruction", () => {
    const span = {
      input: {
        contents: [{ role: "user", parts: [{ text: "What's 2+2?" }] }],
        config: { system_instruction: "You are a math tutor." },
      },
    };
    expect(normalizeMessagesFromSpan(span)).toEqual([
      { role: "system", content: "You are a math tutor." },
      { role: "user", content: "What's 2+2?" },
    ]);
  });
});

describe("extractModelFromSpan", () => {
  it("prefers span.model", () => {
    expect(
      extractModelFromSpan({ model: "gpt-5.2", span_attributes: {} }),
    ).toBe("gpt-5.2");
  });
  it("falls back to gen_ai.request.model then llm.model_name", () => {
    expect(
      extractModelFromSpan({
        span_attributes: { "gen_ai.request.model": "gemini-2.0-flash" },
      }),
    ).toBe("gemini-2.0-flash");
    expect(
      extractModelFromSpan({ span_attributes: { "llm.model_name": "gpt-4o" } }),
    ).toBe("gpt-4o");
  });
  it("returns '' when no model present", () => {
    expect(extractModelFromSpan({ span_attributes: {} })).toBe("");
  });
  // Bug :198 — a non-string model attr must not leak through.
  it("ignores a non-string model attr and falls back", () => {
    expect(
      extractModelFromSpan({
        span_attributes: {
          "gen_ai.request.model": { name: "x" },
          "llm.model_name": "gpt-4o",
        },
      }),
    ).toBe("gpt-4o");
  });
});

describe("extractProviderFromSpan", () => {
  it("reads provider from gen_ai.provider.name / llm.provider / span.provider", () => {
    expect(
      extractProviderFromSpan({
        span_attributes: { "gen_ai.provider.name": "google.generative-ai" },
      }),
    ).toBe("google.generative-ai");
    expect(extractProviderFromSpan({ provider: "openai" })).toBe("openai");
    expect(extractProviderFromSpan({ span_attributes: {} })).toBe("");
  });
});

describe("buildPromptConfigFromSpan return", () => {
  it("includes model, provider, and params alongside messages", () => {
    const span = {
      model: "gpt-5.2",
      span_attributes: {
        "gen_ai.provider.name": "openai",
        "gen_ai.request.temperature": 0.3,
        "gen_ai.input.messages.0.message.role": "user",
        "gen_ai.input.messages.0.message.content": "Hi",
      },
    };
    const { model, provider, parameters } = buildPromptConfigFromSpan(span);
    expect(model).toBe("gpt-5.2");
    expect(provider).toBe("openai");
    expect(parameters).toEqual({ temperature: 0.3 });
  });
});

describe("extractParamsFromSpan", () => {
  it("reads individual gen_ai.request.* numeric params", () => {
    expect(
      extractParamsFromSpan({
        span_attributes: {
          "gen_ai.request.temperature": 0.7,
          "gen_ai.request.max_tokens": 256,
          "gen_ai.request.top_p": 0.9,
        },
      }),
    ).toEqual({ temperature: 0.7, max_tokens: 256, top_p: 0.9 });
  });

  it("reads the gen_ai.request.parameters blob and drops transport junk", () => {
    expect(
      extractParamsFromSpan({
        span_attributes: {
          "gen_ai.request.parameters": JSON.stringify({
            temperature: 0.5,
            _type: "openai-chat",
            model: "gpt-5.2",
            stream: false,
            http_options: { headers: {} },
          }),
        },
      }),
    ).toEqual({ temperature: 0.5 });
  });

  it("returns {} when params are empty/absent", () => {
    expect(
      extractParamsFromSpan({
        span_attributes: { "gen_ai.request.parameters": "" },
      }),
    ).toEqual({});
  });

  // Bug :255 — flattened OTel params are strings ("0.7"); coerce, don't drop.
  it("coerces string-numeric individual params", () => {
    expect(
      extractParamsFromSpan({
        span_attributes: {
          "gen_ai.request.temperature": "0.7",
          "gen_ai.request.max_tokens": "256",
        },
      }),
    ).toEqual({ temperature: 0.7, max_tokens: 256 });
  });

  it("coerces string-numeric params inside the parameters blob and drops non-numeric junk", () => {
    expect(
      extractParamsFromSpan({
        span_attributes: {
          "gen_ai.request.parameters": JSON.stringify({
            top_p: "0.9",
            temperature: "not-a-number",
            model: "gpt-5.2",
          }),
        },
      }),
    ).toEqual({ top_p: 0.9 });
  });
});

describe("extractResponseFormatFromSpan", () => {
  it("returns 'text' when no response_format is present", () => {
    expect(extractResponseFormatFromSpan({ span_attributes: {} })).toBe("text");
  });

  it("derives 'json' from a json_object/json_schema request body", () => {
    expect(
      extractResponseFormatFromSpan({
        input: JSON.stringify({ response_format: { type: "json_object" } }),
      }),
    ).toBe("json");
    expect(
      extractResponseFormatFromSpan({
        input: JSON.stringify({ response_format: { type: "json_schema" } }),
      }),
    ).toBe("json");
  });

  it("derives 'json' from a stringified gen_ai.request.response_format attr", () => {
    expect(
      extractResponseFormatFromSpan({
        span_attributes: {
          "gen_ai.request.response_format": JSON.stringify({
            type: "json_object",
          }),
        },
      }),
    ).toBe("json");
  });

  it("returns 'text' for a non-json response_format", () => {
    expect(
      extractResponseFormatFromSpan({
        input: JSON.stringify({ response_format: { type: "text" } }),
      }),
    ).toBe("text");
  });
});

describe("buildPromptConfigFromSpan", () => {
  it("wraps content in workbench format and keeps an existing system message", () => {
    const span = {
      span_attributes: {
        "gen_ai.input.messages.0.message.role": "system",
        "gen_ai.input.messages.0.message.content": "Sys",
        "gen_ai.input.messages.1.message.role": "user",
        "gen_ai.input.messages.1.message.content": "Hi",
      },
    };
    const { messages } = buildPromptConfigFromSpan(span);
    expect(messages).toEqual([
      { role: "system", content: [{ type: "text", text: "Sys" }] },
      { role: "user", content: [{ type: "text", text: "Hi" }] },
    ]);
  });

  it("prepends an empty system only when none exists", () => {
    const span = {
      span_attributes: {
        "gen_ai.input.messages.0.message.role": "user",
        "gen_ai.input.messages.0.message.content": "Hi",
      },
    };
    const { messages } = buildPromptConfigFromSpan(span);
    expect(messages[0]).toEqual({
      role: "system",
      content: [{ type: "text", text: "" }],
    });
    expect(messages[1].role).toBe("user");
  });

  it("does not prepend a system when one already exists out of order", () => {
    const span = {
      span_attributes: {
        "gen_ai.input.messages.0.message.role": "user",
        "gen_ai.input.messages.0.message.content": "Hi",
        "gen_ai.input.messages.1.message.role": "system",
        "gen_ai.input.messages.1.message.content": "Sys",
      },
    };
    const { messages } = buildPromptConfigFromSpan(span);
    expect(messages.filter((m) => m.role === "system")).toHaveLength(1);
    expect(messages[0].role).toBe("user");
  });

  it("returns no messages for an unparseable object input", () => {
    const { messages } = buildPromptConfigFromSpan({
      span_attributes: {},
      input: { config: {} },
    });
    expect(messages).toEqual([]);
  });

  // Bug :48 — templatize must replace a whole-word value but not a short/common
  // value that would mangle unrelated text.
  it("templatizes a word-boundary value into {{name}}", () => {
    const span = {
      span_attributes: {
        "gen_ai.prompt.template.variables": JSON.stringify({ city: "Mumbai" }),
        "gen_ai.input.messages.0.message.role": "user",
        "gen_ai.input.messages.0.message.content": "Weather in Mumbai today?",
      },
    };
    const { messages, variableNames } = buildPromptConfigFromSpan(span);
    const userMsg = messages.find((m) => m.role === "user");
    expect(userMsg.content[0].text).toBe("Weather in {{city}} today?");
    expect(variableNames).toEqual({ city: ["Mumbai"] });
  });

  it("does not templatize a value embedded inside a larger word", () => {
    const span = {
      span_attributes: {
        "gen_ai.prompt.template.variables": JSON.stringify({ word: "the" }),
        "gen_ai.input.messages.0.message.role": "user",
        "gen_ai.input.messages.0.message.content": "Visit the theater",
      },
    };
    const { messages } = buildPromptConfigFromSpan(span);
    const userMsg = messages.find((m) => m.role === "user");
    // "the" standalone is replaced; "the" inside "theater" is left intact.
    expect(userMsg.content[0].text).toBe("Visit {{word}} theater");
  });

  it("skips templatizing a 1-char value that would match everywhere", () => {
    const span = {
      span_attributes: {
        "gen_ai.prompt.template.variables": JSON.stringify({ n: "1" }),
        "gen_ai.input.messages.0.message.role": "user",
        "gen_ai.input.messages.0.message.content": "1 plus 1 is 11",
      },
    };
    const { messages } = buildPromptConfigFromSpan(span);
    const userMsg = messages.find((m) => m.role === "user");
    expect(userMsg.content[0].text).toBe("1 plus 1 is 11");
  });
});

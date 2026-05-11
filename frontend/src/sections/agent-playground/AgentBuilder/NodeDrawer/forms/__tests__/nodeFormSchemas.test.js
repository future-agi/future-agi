import { describe, it, expect } from "vitest";
import {
  getPromptNodeFormSchema,
  agentNodeFormSchema,
  getNodeFormSchema,
} from "../nodeFormSchemas";
import { NODE_TYPES } from "../../../../utils/constants";

// ---------------------------------------------------------------------------
// getPromptNodeFormSchema
// ---------------------------------------------------------------------------
describe("getPromptNodeFormSchema", () => {
  const schema = getPromptNodeFormSchema();

  const validForm = {
    name: "my_prompt",
    modelConfig: {
      model: "gpt-4",
      modelDetail: { modelName: "GPT-4" },
    },
    messages: [
      {
        id: "msg-0",
        role: "user",
        content: [{ type: "text", text: "Hello" }],
      },
    ],
  };

  it("accepts valid form data", () => {
    const result = schema.safeParse(validForm);
    expect(result.success).toBe(true);
  });

  it("rejects empty name", () => {
    const result = schema.safeParse({ ...validForm, name: "" });
    expect(result.success).toBe(false);
  });

  it("rejects name with uppercase or spaces", () => {
    const result = schema.safeParse({ ...validForm, name: "My Prompt" });
    expect(result.success).toBe(false);
  });

  it("rejects empty model", () => {
    const result = schema.safeParse({
      ...validForm,
      modelConfig: { ...validForm.modelConfig, model: "" },
    });
    expect(result.success).toBe(false);
  });

  it("rejects empty messages array", () => {
    const result = schema.safeParse({ ...validForm, messages: [] });
    expect(result.success).toBe(false);
  });

  it("rejects user message with empty text content", () => {
    const result = schema.safeParse({
      ...validForm,
      messages: [
        {
          id: "msg-1",
          role: "user",
          content: [{ type: "text", text: "" }],
        },
      ],
    });
    expect(result.success).toBe(false);
  });

  it("accepts user message with image_url content", () => {
    const result = schema.safeParse({
      ...validForm,
      messages: [
        {
          id: "msg-1",
          role: "user",
          content: [
            {
              type: "image_url",
              imageUrl: { url: "https://example.com/img.png" },
            },
          ],
        },
      ],
    });
    expect(result.success).toBe(true);
  });

  it("accepts user message with pdf_url content", () => {
    const result = schema.safeParse({
      ...validForm,
      messages: [
        {
          id: "msg-1",
          role: "user",
          content: [
            {
              type: "pdf_url",
              pdfUrl: { url: "https://example.com/doc.pdf" },
            },
          ],
        },
      ],
    });
    expect(result.success).toBe(true);
  });

  it("accepts user message with audio_url content", () => {
    const result = schema.safeParse({
      ...validForm,
      messages: [
        {
          id: "msg-1",
          role: "user",
          content: [
            {
              type: "audio_url",
              audioUrl: { url: "https://example.com/audio.mp3" },
            },
          ],
        },
      ],
    });
    expect(result.success).toBe(true);
  });

  it("accepts system message with empty content (no user validation)", () => {
    const result = schema.safeParse({
      ...validForm,
      messages: [
        { id: "msg-1", role: "system", content: [{ type: "text", text: "" }] },
        {
          id: "msg-2",
          role: "user",
          content: [{ type: "text", text: "Hello" }],
        },
      ],
    });
    expect(result.success).toBe(true);
  });

  it("accepts assistant message with empty content", () => {
    const result = schema.safeParse({
      ...validForm,
      messages: [
        {
          id: "msg-1",
          role: "assistant",
          content: [{ type: "text", text: "" }],
        },
        {
          id: "msg-2",
          role: "user",
          content: [{ type: "text", text: "Hello" }],
        },
      ],
    });
    expect(result.success).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// agentNodeFormSchema
// ---------------------------------------------------------------------------
describe("agentNodeFormSchema", () => {
  const validAgent = {
    name: "my_agent",
    graphId: "graph-1",
    versionId: "ver-1",
  };

  it("accepts valid agent data", () => {
    const result = agentNodeFormSchema.safeParse(validAgent);
    expect(result.success).toBe(true);
  });

  it("rejects missing name", () => {
    const result = agentNodeFormSchema.safeParse({
      ...validAgent,
      name: "",
    });
    expect(result.success).toBe(false);
  });

  it("rejects missing graphId", () => {
    const result = agentNodeFormSchema.safeParse({
      ...validAgent,
      graphId: "",
    });
    expect(result.success).toBe(false);
  });

  it("rejects missing versionId", () => {
    const result = agentNodeFormSchema.safeParse({
      ...validAgent,
      versionId: "",
    });
    expect(result.success).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// getNodeFormSchema
// ---------------------------------------------------------------------------
describe("getNodeFormSchema", () => {
  it("returns prompt schema for LLM_PROMPT type", () => {
    const schema = getNodeFormSchema(NODE_TYPES.LLM_PROMPT);
    // Prompt schema requires messages
    const result = schema.safeParse({
      name: "test",
      modelConfig: { model: "gpt-4" },
    });
    expect(result.success).toBe(false); // missing messages
  });

  it("returns agent schema for AGENT type", () => {
    const schema = getNodeFormSchema(NODE_TYPES.AGENT);
    const result = schema.safeParse({
      name: "agent",
      graphId: "g1",
      versionId: "v1",
    });
    expect(result.success).toBe(true);
  });

  it("returns eval schema for evaluation type", () => {
    const schema = getNodeFormSchema(NODE_TYPES.EVAL);
    const result = schema.safeParse({ name: "eval_node" });
    expect(result.success).toBe(true);
  });

  it("returns basic schema for unknown type", () => {
    const schema = getNodeFormSchema("unknown_type");
    const result = schema.safeParse({ name: "unknown" });
    expect(result.success).toBe(true);
  });

  it("basic schema for unknown type rejects empty name", () => {
    const schema = getNodeFormSchema("unknown_type");
    const result = schema.safeParse({ name: "" });
    expect(result.success).toBe(false);
  });
});

/**
 * Contract tests for the generated ModelHubExperimentsV2CreateBody zod schema.
 *
 * These test the GENERATED client — the layer the frontend actually imports —
 * not the backend runtime. That is the layer that has silently broken across
 * multiple rounds of this PR.
 *
 * NOTE: this file lives outside src/generated/ on purpose. orval wipes the
 * generated directory on every codegen run, so any test inside that tree
 * silently disappears.
 */
import { describe, it, expect } from "vitest";
import {
  ModelHubExperimentsV2CreateBody,
  ModelHubExperimentsV2UpdateBody,
} from "src/generated/api-contracts/api.zod";

const MINIMAL_PAYLOAD = {
  name: "my experiment",
  dataset_id: "00000000-0000-0000-0000-000000000001",
  experiment_type: "llm",
  prompt_config: [],
  user_eval_metrics: [],
};

const FULL_PROMPT_CONFIG_ITEM = {
  id: "00000000-0000-0000-0000-000000000002",
  name: "gpt config",
  model: "turing_large",            // bare string — this was the original bug
  model_params: {
    temperature: 0.7,
    max_tokens: 1000,
    response_format: "json_object", // string response_format
    custom_provider_key: "value",   // unknown provider-specific key
  },
  configuration: {
    tool_choice: "auto",
    custom_field: "custom_value",   // unknown key must pass through
  },
  output_format: "string",
  messages: [
    {
      role: "user",
      content: "Hello, world!",     // string content — the other original bug
      id: "client-generated-id",    // client-side id used as React key
    },
    {
      role: "assistant",
      content: [{ type: "text", text: "Hi" }],  // array content
    },
  ],
};

describe("ModelHubExperimentsV2CreateBody generated zod contract", () => {
  it("accepts a minimal payload", () => {
    const result = ModelHubExperimentsV2CreateBody.safeParse(MINIMAL_PAYLOAD);
    expect(result.success).toBe(true);
  });

  it("accepts a bare string model (the headline regression)", () => {
    const result = ModelHubExperimentsV2CreateBody.safeParse({
      ...MINIMAL_PAYLOAD,
      prompt_config: [{ ...FULL_PROMPT_CONFIG_ITEM, model: "turing_large" }],
    });
    expect(result.success).toBe(true);
  });

  it("accepts a model-spec object model", () => {
    const result = ModelHubExperimentsV2CreateBody.safeParse({
      ...MINIMAL_PAYLOAD,
      prompt_config: [{ ...FULL_PROMPT_CONFIG_ITEM, model: { name: "gpt-4o", version: "2024" } }],
    });
    expect(result.success).toBe(true);
  });

  it("accepts a string response_format", () => {
    const result = ModelHubExperimentsV2CreateBody.safeParse({
      ...MINIMAL_PAYLOAD,
      prompt_config: [FULL_PROMPT_CONFIG_ITEM],
    });
    expect(result.success).toBe(true);
  });

  it("accepts omitted model_params and configuration", () => {
    const { model_params, configuration, ...rest } = FULL_PROMPT_CONFIG_ITEM;
    const result = ModelHubExperimentsV2CreateBody.safeParse({
      ...MINIMAL_PAYLOAD,
      prompt_config: [rest],
    });
    expect(result.success).toBe(true);
  });

  it("passes through unknown provider-specific keys in model_params", () => {
    const result = ModelHubExperimentsV2CreateBody.safeParse({
      ...MINIMAL_PAYLOAD,
      prompt_config: [FULL_PROMPT_CONFIG_ITEM],
    });
    expect(result.success).toBe(true);
    const parsed = result.data.prompt_config[0];
    expect(parsed.model_params.custom_provider_key).toBe("value");
  });

  it("accepts a message with client-side id and string content", () => {
    const result = ModelHubExperimentsV2CreateBody.safeParse({
      ...MINIMAL_PAYLOAD,
      prompt_config: [FULL_PROMPT_CONFIG_ITEM],
    });
    expect(result.success).toBe(true);
    const msg = result.data.prompt_config[0].messages[0];
    expect(msg.id).toBe("client-generated-id");
    expect(msg.content).toBe("Hello, world!");
  });

  it("accepts a message with array content", () => {
    const result = ModelHubExperimentsV2CreateBody.safeParse({
      ...MINIMAL_PAYLOAD,
      prompt_config: [FULL_PROMPT_CONFIG_ITEM],
    });
    expect(result.success).toBe(true);
    const msg = result.data.prompt_config[0].messages[1];
    expect(Array.isArray(msg.content)).toBe(true);
  });

  // Pins MessageItem .passthrough() — the extra key must be one that is NOT
  // declared in the serializer (`id` is declared, so it can't catch the drop).
  // Backend swagger has additionalProperties:true on MessageItem; without a
  // dedicated .passthrough() rewrite the generated zod silently strips unknown
  // message keys.
  it("passes through unknown keys in a message", () => {
    const result = ModelHubExperimentsV2CreateBody.safeParse({
      ...MINIMAL_PAYLOAD,
      prompt_config: [
        {
          ...FULL_PROMPT_CONFIG_ITEM,
          messages: [
            {
              role: "user",
              content: "hi",
              provider_metadata: { source: "sdk" },
            },
          ],
        },
      ],
    });
    expect(result.success).toBe(true);
    expect(result.data.prompt_config[0].messages[0].provider_metadata).toEqual({
      source: "sdk",
    });
  });
});

describe("ModelHubExperimentsV2UpdateBody generated zod contract", () => {
  it("accepts a string model on update", () => {
    const result = ModelHubExperimentsV2UpdateBody.safeParse({
      prompt_config: [{ model: "turing_large" }],
    });
    expect(result.success).toBe(true);
  });

  it("accepts a string response_format on update", () => {
    const result = ModelHubExperimentsV2UpdateBody.safeParse({
      prompt_config: [{ model_params: { response_format: "json_object" } }],
    });
    expect(result.success).toBe(true);
  });
});

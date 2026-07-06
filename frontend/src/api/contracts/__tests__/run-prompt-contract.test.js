import { describe, it, expect } from "vitest";
import { validateContractedRequestConfig } from "../openapi-contract";

// Round-trip guard (TH-6280): a representative run payload must pass the
// add_run_prompt_column request contract. The PromptConfig serializer used
// loose DictField/JSONField fields that generated an over-constrained contract
// (every value typed `string`), which silently blocked the request in dev.
// This pins the real shape so a too-loose serializer can't regress it again.
const runRequest = {
  url: "/model-hub/develops/add_run_prompt_column/",
  method: "post",
  data: {
    dataset_id: "9ea714d2-c215-499c-8797-b9ae52d6d42a",
    name: "run",
    config: {
      model: "gpt-4o-mini",
      run_prompt_config: {
        model_name: "gpt-4o-mini",
        providers: "openai",
        isAvailable: true, // boolean (was: Expected string)
        booleans: {}, // object  (was: Expected string)
        dropdowns: {}, // object  (was: Expected string)
        temperature: null,
        top_p: null,
      },
      messages: [
        { role: "system", content: [{ type: "text", text: "" }] },
        // array content (was: Expected string)
        { role: "user", content: [{ type: "text", text: "hi" }] },
      ],
      response_format: "text", // string  (was: Expected object)
      tools: [],
    },
  },
};

describe("add_run_prompt_column request contract", () => {
  it("accepts a real run payload (mixed run_prompt_config, array message content, string response_format)", () => {
    const result = validateContractedRequestConfig(runRequest);
    expect(result.ok).toBe(true);
  });

  it("accepts legacy configuration.template_format as a scalar string", () => {
    const result = validateContractedRequestConfig({
      ...runRequest,
      data: {
        ...runRequest.data,
        config: {
          ...runRequest.data.config,
          run_prompt_config: {},
          configuration: {
            template_format: "jinja",
          },
        },
      },
    });

    expect(result.ok).toBe(true);
  });
});

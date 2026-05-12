import { describe, it, expect } from "vitest";
import { stripAttributePathPrefix } from "../utils";

describe("stripAttributePathPrefix", () => {
  it("strips a bare span_attributes prefix", () => {
    expect(stripAttributePathPrefix("span_attributes.input.value")).toBe(
      "input.value",
    );
  });

  it("strips the voice-detail observation_span.<n>.span_attributes wrapper", () => {
    expect(
      stripAttributePathPrefix(
        "observation_span.0.span_attributes.conversation.recording.mono.combined",
      ),
    ).toBe("conversation.recording.mono.combined");
  });

  it("strips the wrapper for multi-digit indices", () => {
    expect(
      stripAttributePathPrefix("observation_span.42.span_attributes.transcript"),
    ).toBe("transcript");
  });

  it("strips the wrapper for top-level span fields (no span_attributes segment)", () => {
    expect(stripAttributePathPrefix("observation_span.0.model")).toBe("model");
    expect(stripAttributePathPrefix("observation_span.0.latency_ms")).toBe(
      "latency_ms",
    );
    expect(stripAttributePathPrefix("observation_span.7.prompt_tokens")).toBe(
      "prompt_tokens",
    );
  });

  it("returns the input unchanged when no prefix matches", () => {
    expect(stripAttributePathPrefix("input.value")).toBe("input.value");
  });

  it("returns an empty string for empty input", () => {
    expect(stripAttributePathPrefix("")).toBe("");
  });

  it("strips a span_attributes segment anywhere in the path, not only as a prefix", () => {
    // The `span_attributes.` strip is intentionally unanchored — see the
    // implementation comment at src/utils/utils.js. The FE walker over a
    // fetched span detail hits `span_attributes.` mid-path; collapsing both
    // forms keeps fieldSet and flatValueMap lookups aligned with the saved
    // mapping (which uses bare attribute paths).
    expect(stripAttributePathPrefix("metadata.span_attributes.input")).toBe(
      "metadata.input",
    );
  });
});

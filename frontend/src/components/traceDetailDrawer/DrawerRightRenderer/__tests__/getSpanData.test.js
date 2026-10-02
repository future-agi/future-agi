import { describe, it, expect } from "vitest";

import { getLlmData } from "../getSpanData";

// Characterization tests pinning current getLlmData output. A snapshot change is a
// real behaviour change to review, not something to blindly update.

const input = (attrs) => getLlmData({ span_attributes: attrs }, "input");
const output = (attrs) => getLlmData({ span_attributes: attrs }, "output");

describe("getLlmData / extractMessages (characterization)", () => {
  it("parses single-content snake_case input messages", () => {
    expect(
      input({
        "llm.input_messages.0.message.role": "system",
        "llm.input_messages.0.message.content": "You are helpful.",
        "llm.input_messages.1.message.role": "user",
        "llm.input_messages.1.message.content": "Hi there",
      }).inputMessage,
    ).toMatchInlineSnapshot(`
      [
        {
          "content": [
            "You are helpful.",
          ],
          "role": "system",
        },
        {
          "content": [
            "Hi there",
          ],
          "role": "user",
        },
      ]
    `);
  });

  it("parses camelCase (OpenInference) prefixes", () => {
    expect(
      input({
        "llm.inputMessages.0.message.role": "user",
        "llm.inputMessages.0.message.content": "camel hello",
      }).inputMessage,
    ).toMatchInlineSnapshot(`
      [
        {
          "content": [
            "camel hello",
          ],
          "role": "user",
        },
      ]
    `);
  });

  it("parses gen_ai OTEL GenAI prefixes", () => {
    expect(
      input({
        "gen_ai.input.messages.0.message.role": "user",
        "gen_ai.input.messages.0.message.content": "genai hello",
      }).inputMessage,
    ).toMatchInlineSnapshot(`
      [
        {
          "content": [
            "genai hello",
          ],
          "role": "user",
        },
      ]
    `);
  });

  it("parses bare role/content properties (no message. wrapper)", () => {
    expect(
      input({
        "llm.input_messages.0.role": "assistant",
        "llm.input_messages.0.content": "bare content",
      }).inputMessage,
    ).toMatchInlineSnapshot(`
      [
        {
          "content": [
            "bare content",
          ],
          "role": "assistant",
        },
      ]
    `);
  });

  it("drops a message that has a role but no content", () => {
    expect(
      input({
        "llm.input_messages.0.message.role": "user",
      }).inputMessage,
    ).toMatchInlineSnapshot(`[]`);
  });

  it("drops a message that has content but no role", () => {
    expect(
      input({
        "llm.input_messages.0.message.content": "orphan content",
      }).inputMessage,
    ).toMatchInlineSnapshot(`[]`);
  });

  it("stringifies object-valued content", () => {
    expect(
      input({
        "llm.input_messages.0.message.role": "user",
        "llm.input_messages.0.message.content": { foo: "bar", n: 1 },
      }).inputMessage,
    ).toMatchInlineSnapshot(`
      [
        {
          "content": [
            "{
        "foo": "bar",
        "n": 1
      }",
          ],
          "role": "user",
        },
      ]
    `);
  });

  it("parses multi-part nested content shapes", () => {
    expect(
      input({
        "llm.input_messages.0.message.role": "user",
        "llm.input_messages.0.message.contents.0.message_content.type": "text",
        "llm.input_messages.0.message.contents.0.message_content.text": "part one",
        "llm.input_messages.0.message.contents.1.message_content.type": "text",
        "llm.input_messages.0.message.contents.1.message_content.text": "part two",
      }).inputMessage,
    ).toMatchInlineSnapshot(`
      [
        {
          "content": [
            {},
            {},
          ],
          "role": "user",
        },
      ]
    `);
  });

  it("orders messages by numeric index, not string index", () => {
    expect(
      input({
        "llm.input_messages.10.message.role": "user",
        "llm.input_messages.10.message.content": "tenth",
        "llm.input_messages.2.message.role": "user",
        "llm.input_messages.2.message.content": "second",
      }).inputMessage,
    ).toMatchInlineSnapshot(`
      [
        {
          "content": [
            "second",
          ],
          "role": "user",
        },
        {
          "content": [
            "tenth",
          ],
          "role": "user",
        },
      ]
    `);
  });

  it("parses output messages via the output type", () => {
    expect(
      output({
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.content": "the answer",
      }).outputMessage,
    ).toMatchInlineSnapshot(`
      [
        {
          "content": [
            "the answer",
          ],
          "role": "assistant",
        },
      ]
    `);
  });

  it("returns an empty list when no message attributes are present", () => {
    expect(input({ "some.other.attr": "x" }).inputMessage).toMatchInlineSnapshot(`[]`);
  });

  it("keeps a flat content string when a message also has structured parts (no crash)", () => {
    expect(() =>
      input({
        "llm.input_messages.0.message.role": "user",
        "llm.input_messages.0.message.content": "[image: masked]",
        "llm.input_messages.0.message.contents.0.message_content.image.url":
          "data:image/png;base64,xxx",
      }),
    ).not.toThrow();
    expect(
      input({
        "llm.input_messages.0.message.role": "user",
        "llm.input_messages.0.message.content": "[image: masked]",
        "llm.input_messages.0.message.contents.0.message_content.image.url":
          "data:image/png;base64,xxx",
      }).inputMessage,
    ).toMatchInlineSnapshot(`
      [
        {
          "content": [
            "[image: masked]",
          ],
          "role": "user",
        },
      ]
    `);
  });
});

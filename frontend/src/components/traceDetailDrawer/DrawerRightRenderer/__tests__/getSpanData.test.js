import { describe, it, expect } from "vitest";

import { getLlmData } from "../getSpanData";
import { messageBlockText } from "src/utils/message-text";
import {
  openaiImageAttributes,
  openaiImageInput,
  openaiImageOutput,
} from "./openaiMultimodalSpan";

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

  // This snapshot used to pin `{}` / `{}`: the path parser cut `content.` out of
  // `message_content.text` and lost every field (TH-2379). Parts keep them now.
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
            {
              "text": "part one",
              "type": "text",
            },
            {
              "text": "part two",
              "type": "text",
            },
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

describe("unit: flattened multimodal message parts (TH-2379)", () => {
  const span = (attrs) => ({ span_attributes: attrs });
  const PART = "llm.input_messages.0.message.contents";

  it("keeps the user's text beside the image part of the actual OpenAI SDK span", () => {
    const flat = {
      input: openaiImageInput,
      output: openaiImageOutput,
      span_attributes: openaiImageAttributes,
    };
    const { inputMessage } = getLlmData(flat, "input");
    expect(inputMessage).toEqual([
      { role: "system", content: ["Describe the image the user sends."] },
      {
        role: "user",
        content: [
          { type: "text", text: "What colour is this square?" },
          {
            type: "image",
            image: "fi-attachment:5f3f1b6d-d148-4cc6-bce5-1221374ca520",
          },
        ],
      },
    ]);
    // Message 2, part 2 is where the attachment reference places the image.
    expect(messageBlockText(inputMessage[1].content[0])).toBe(
      "What colour is this square?",
    );
    expect(getLlmData(flat, "output").outputMessage).toEqual([
      {
        role: "assistant",
        content: ["A teal square with a darker diagonal band."],
      },
    ]);
  });

  it("orders parts by part number, whatever order the attributes arrive in", () => {
    const [message] = input({
      [`${PART}.10.message_content.text`]: "eleventh",
      [`${PART}.2.message_content.type`]: "image",
      "llm.input_messages.0.message.role": "user",
      [`${PART}.0.message_content.text`]: "first",
      [`${PART}.2.message_content.image`]: "fi-attachment:third",
      [`${PART}.10.message_content.type`]: "text",
      [`${PART}.0.message_content.type`]: "text",
    }).inputMessage;
    expect(message.content).toEqual([
      { type: "text", text: "first" },
      { type: "image", image: "fi-attachment:third" },
      { type: "text", text: "eleventh" },
    ]);
  });

  it("keeps literal text that looks like a content path, and fields named content", () => {
    const literal =
      "Read message.contents.0.message_content.text, then content. and contents.1";
    const [message] = input({
      "llm.input_messages.0.message.role": "user",
      [`${PART}.0.message_content.text`]: literal,
      [`${PART}.1.message_content.type`]: "text",
      [`${PART}.1.message_content.content`]: "message_content.text",
      [`${PART}.2.message_content.text_content`]: "contents.",
    }).inputMessage;
    expect(message.content).toEqual([
      { text: literal },
      { type: "text", content: "message_content.text" },
      { text_content: "contents." },
    ]);
  });

  it("still reads the camelCase messageContent wrapper", () => {
    const [message] = input({
      "llm.inputMessages.0.message.role": "user",
      "llm.inputMessages.0.message.contents.0.messageContent.type": "text",
      "llm.inputMessages.0.message.contents.0.messageContent.text": "camel",
      "llm.inputMessages.0.message.contents.1.messageContent.type": "image",
      "llm.inputMessages.0.message.contents.1.messageContent.image.url":
        "data:image/png;base64,AAAA",
    }).inputMessage;
    expect(message.content).toEqual([
      { type: "text", text: "camel" },
      { type: "image", "image.url": "data:image/png;base64,AAAA" },
    ]);
  });

  it("keeps missing, unwrapped and malformed parts visible instead of dropping them", () => {
    const [message] = input({
      "llm.input_messages.0.message.role": "user",
      [`${PART}.0.text`]: "unwrapped text",
      [`${PART}.1.custom_wrapper.kind`]: "vendor part",
      [`${PART}.2`]: "a whole part",
      [`${PART}.3.message_content.type`]: "image",
      [`${PART}.7.message_content`]: "wrapper only",
    }).inputMessage;
    // Part numbers 4-6 are absent: no empty slots, the rest stay in order.
    expect(Object.keys(message.content)).toEqual(["0", "1", "2", "3", "4"]);
    expect(message.content).toEqual([
      { text: "unwrapped text" },
      { "custom_wrapper.kind": "vendor part" },
      "a whole part",
      { type: "image" },
      "wrapper only",
    ]);
    // A part with no text is shown as its JSON, never as an empty object.
    expect(message.content.map(messageBlockText)).toEqual([
      "unwrapped text",
      '{"custom_wrapper.kind":"vendor part"}',
      "a whole part",
      '{"type":"image"}',
      "wrapper only",
    ]);
  });

  it("does not attach fields to a scalar part or let a whole part clobber fields", () => {
    const [message] = input({
      "llm.input_messages.0.message.role": "user",
      [`${PART}.0`]: "whole part first",
      [`${PART}.0.message_content.text`]: "ignored field",
      [`${PART}.1.message_content.text`]: "fields first",
      [`${PART}.1`]: "ignored whole part",
    }).inputMessage;
    expect(message.content).toEqual([
      "whole part first",
      { text: "fields first" },
    ]);
  });

  it("keeps structured parts when the flat summary arrives after them (no crash)", () => {
    const attrs = {
      "llm.input_messages.0.message.role": "user",
      [`${PART}.0.message_content.image.url`]: "data:image/png;base64,xxx",
      "llm.input_messages.0.message.content": "[image: masked]",
    };
    expect(() => input(attrs)).not.toThrow();
    expect(input(attrs).inputMessage[0].content).toEqual([
      { "image.url": "data:image/png;base64,xxx" },
    ]);
  });

  it("serializes object values safely and keeps a __proto__ field as data", () => {
    const value = {
      toJSON() {
        throw new Error("must not run");
      },
      n: 1,
    };
    const [message] = input({
      "llm.input_messages.0.message.role": "user",
      [`${PART}.0.message_content.__proto__`]: "not a prototype",
      [`${PART}.1.message_content.data`]: value,
    }).inputMessage;
    const [first, second] = message.content;
    expect(Object.getPrototypeOf(first)).toBe(Object.prototype);
    expect(Object.hasOwn(first, "__proto__")).toBe(true);
    expect(Object.getOwnPropertyDescriptor(first, "__proto__").value).toBe(
      "not a prototype",
    );
    expect(second.data).toBe('{\n  "toJSON": "[Function]",\n  "n": 1\n}');
  });

  it("leaves plain messages beside a multimodal one unchanged", () => {
    const messages = getLlmData(
      span({
        "gen_ai.input.messages.0.message.role": "system",
        "gen_ai.input.messages.0.message.content": "Be brief.",
        "gen_ai.input.messages.1.role": "user",
        "gen_ai.input.messages.1.content": "plain content.",
        "gen_ai.input.messages.2.message.role": "user",
        "gen_ai.input.messages.2.message.contents.0.message_content.type":
          "text",
        "gen_ai.input.messages.2.message.contents.0.message_content.text":
          "with a part",
      }),
      "input",
    ).inputMessage;
    expect(messages).toEqual([
      { role: "system", content: ["Be brief."] },
      { role: "user", content: ["plain content."] },
      { role: "user", content: [{ type: "text", text: "with a part" }] },
    ]);
  });
});

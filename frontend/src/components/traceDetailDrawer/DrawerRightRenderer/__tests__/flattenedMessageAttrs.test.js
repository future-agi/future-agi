import { describe, it, expect } from "vitest";

import {
  groupFlattenedMessageAttrs,
  messageAttrPrefixes,
} from "../flattenedMessageAttrs";

describe("messageAttrPrefixes", () => {
  it("returns the three recognised prefixes for a message type", () => {
    expect(messageAttrPrefixes("input")).toEqual([
      "llm.inputMessages",
      "llm.input_messages",
      "gen_ai.input.messages",
    ]);
    expect(messageAttrPrefixes("output")).toEqual([
      "llm.outputMessages",
      "llm.output_messages",
      "gen_ai.output.messages",
    ]);
  });
});

describe("groupFlattenedMessageAttrs", () => {
  it("groups entries by message index with property/value pairs", () => {
    expect(
      groupFlattenedMessageAttrs({
        "llm.input_messages.0.message.role": "user",
        "llm.input_messages.0.message.content": "hi",
      }),
    ).toEqual([
      {
        index: "0",
        entries: [
          { property: "message.role", value: "user" },
          { property: "message.content", value: "hi" },
        ],
      },
    ]);
  });

  it("recognises all three prefix conventions", () => {
    const grouped = (key) =>
      groupFlattenedMessageAttrs({ [key]: "x" })[0]?.entries[0]?.value;
    expect(grouped("llm.inputMessages.0.message.role")).toBe("x");
    expect(grouped("llm.input_messages.0.message.role")).toBe("x");
    expect(grouped("gen_ai.input.messages.0.message.role")).toBe("x");
  });

  it("scopes prefixes to the requested message type", () => {
    const attrs = { "llm.output_messages.0.message.role": "assistant" };
    expect(groupFlattenedMessageAttrs(attrs, "input")).toEqual([]);
    expect(groupFlattenedMessageAttrs(attrs, "output")).toHaveLength(1);
  });

  it("orders messages by numeric index, not string index", () => {
    expect(
      groupFlattenedMessageAttrs({
        "llm.input_messages.10.message.role": "user",
        "llm.input_messages.2.message.role": "user",
      }).map((m) => m.index),
    ).toEqual(["2", "10"]);
  });

  it("preserves the full property path after the message index", () => {
    expect(
      groupFlattenedMessageAttrs({
        "gen_ai.input.messages.0.message.contents.1.message_content.text": "t",
      })[0].entries[0].property,
    ).toBe("message.contents.1.message_content.text");
  });

  it("ignores a bare prefix with no message index", () => {
    expect(groupFlattenedMessageAttrs({ "llm.input_messages": [] })).toEqual([]);
  });

  it("ignores a prefixed key that has an index but no property", () => {
    expect(
      groupFlattenedMessageAttrs({ "llm.input_messages.0": "x" }),
    ).toEqual([]);
  });

  it("ignores non-message attributes", () => {
    expect(
      groupFlattenedMessageAttrs({ "gen_ai.request.model": "gpt-4o" }),
    ).toEqual([]);
  });

  it("returns an empty array for nullish attrs", () => {
    expect(groupFlattenedMessageAttrs(null)).toEqual([]);
    expect(groupFlattenedMessageAttrs(undefined)).toEqual([]);
  });
});

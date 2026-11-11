import { describe, expect, it, vi } from "vitest";
import {
  messageBlockText,
  serializeMessageValue,
} from "src/utils/message-text";
import { isOpenAIMessages } from "../ChatMessageView";

describe("unit: safe message text", () => {
  it.each(["hello", "", 0, 42])("preserves scalar text %j", (text) => {
    expect(messageBlockText({ text })).toBe(String(text));
  });
  it.each([
    { text: { toString: 42 } },
    { text: { valueOf: {} } },
    { text: { toJSON: 42 } },
    { content: { text: { toString: 42 } } },
    { delta: { text: { toString: 42 } } },
  ])("keeps the whole malformed block %j", (block) => {
    expect(messageBlockText(block)).toBe(JSON.stringify(block));
  });
  it("never invokes coercion hooks or getters, even at the root", () => {
    const hook = vi.fn(() => {
      throw new Error("must not run");
    });
    const value = { toString: hook, valueOf: hook, toJSON: hook };
    Object.defineProperty(value, "text", { enumerable: true, get: hook });
    expect(messageBlockText(value)).toContain('"text":"[Accessor]"');
    expect(serializeMessageValue(value)).toContain('"toJSON":"[Function]"');
    expect(hook).not.toHaveBeenCalled();
  });
  it("handles cycles without treating repeated references as circular", () => {
    const shared = { count: 123n, symbol: Symbol("marker") };
    const value = { first: shared, second: shared };
    value.self = value;
    const parsed = JSON.parse(serializeMessageValue(value));
    expect(parsed.first).toEqual({ count: "123", symbol: "Symbol(marker)" });
    expect(parsed.second).toEqual(parsed.first);
    expect(parsed.self).toBe("[Circular]");
  });
  it("bounds deeply nested telemetry", () => {
    let value = {};
    for (let i = 0; i < 1000; i += 1) value = { nested: value };
    expect(serializeMessageValue(value)).toContain("[Max depth]");
  });
  it("keeps prototype-named JSON keys as data", () => {
    const text = '{"__proto__":{"text":"reply"},"constructor":42}';
    expect(serializeMessageValue(JSON.parse(text))).toBe(text);
  });
  it.each([null, "null", [null], "[null]"])(
    "does not misdetect null input %j",
    (input) => {
      expect(isOpenAIMessages(input)).toBe(false);
    },
  );
});

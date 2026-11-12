// W6 A4: a path on a conversation cell reads its turns: [-1] is the last
// customer turn, [-1:agent] the last agent turn, [n] the nth turn (0-based
// like every FA Path), [*] every turn; other values are not turn paths.
import { describe, expect, it } from "vitest";
import {
  conversationPathValue,
  mappedConversationValue,
  parseConversationPath,
} from "../conversationPaths";

const conv = {
  turns: [
    { n: 1, role: "customer", content: "My order A-1 is late." },
    { n: 2, role: "agent", content: "Sorry, checking now." },
    { n: 3, role: "customer", content: "Any news?" },
    { n: 4, role: "agent", content: "It ships today.", ticket: { id: "T-9" } },
  ],
  failing_turn: 2,
};

describe("conversationPaths", () => {
  it("parses turn paths only", () => {
    expect(parseConversationPath("[-1].content")).toEqual({
      index: -1,
      role: null,
      field: "content",
    });
    expect(parseConversationPath("$[-1:agent].text")).toEqual({
      index: -1,
      role: "agent",
      field: "text",
    });
    expect(parseConversationPath("[*]")).toEqual({
      index: "*",
      role: null,
      field: null,
    });
    expect(parseConversationPath("turns[0].content")).toBeNull();
    expect(parseConversationPath("[0].a.b")).toBeNull();
  });

  it("reads customer turns from the end, agent turns on request, any turn by position", () => {
    expect(conversationPathValue(conv, "[-1].content")).toEqual({
      found: true,
      text: "Any news?",
    });
    expect(conversationPathValue(conv, "[-2].content").text).toBe(
      "My order A-1 is late.",
    );
    expect(conversationPathValue(conv, "[-1:agent].content").text).toBe(
      "It ships today.",
    );
    expect(conversationPathValue(conv, "[-1:any].role").text).toBe("agent");
    expect(conversationPathValue(conv, "[0].content").text).toBe(
      "My order A-1 is late.",
    );
    expect(conversationPathValue(conv, "[3].ticket").text).toBe('{"id":"T-9"}');
    expect(conversationPathValue(conv, "[*:customer]").text).toBe(
      "My order A-1 is late.\nAny news?",
    );
    expect(conversationPathValue(conv, "[-3].content")).toEqual({
      found: false,
      text: "",
    });
  });

  it("reads {messages} objects and JSON strings; a bare array is left to faPath; not a conversation is null", () => {
    const msgs = [
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello" },
    ];
    expect(conversationPathValue({ messages: msgs }, "[-1].content").text).toBe(
      "hi",
    );
    expect(conversationPathValue(msgs, "[-1].content")).toBeNull();
    expect(
      conversationPathValue(JSON.stringify(msgs), "[-1].content"),
    ).toBeNull();
    expect(
      conversationPathValue(
        JSON.stringify({ messages: msgs }),
        "[-1:assistant]",
      ).text,
    ).toBe("hello");
    expect(conversationPathValue({ a: 1 }, "[-1].content")).toBeNull();
    expect(conversationPathValue("plain text", "[-1]")).toBeNull();
  });

  it("reads a mapping value on a cell", () => {
    expect(mappedConversationValue("col-1::$[-1].content", conv).text).toBe(
      "Any news?",
    );
    expect(mappedConversationValue("col-1", conv)).toBeNull();
    expect(
      mappedConversationValue({ kind: "constant", value: "x" }, conv),
    ).toBeNull();
  });
});

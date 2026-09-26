import { describe, expect, it } from "vitest";
import { normalizeMessagesForLoad } from "../common";
import { normalizeContentBlocks } from "src/components/PromptCards/common";
import {
  extractVariables,
  isContentNotEmpty,
} from "../Playground/common";

// The backend types `Message.content` as `str`
// (futureagi/model_hub/models/run_prompt.py), so templates saved through the
// SDK/API — and versions predating the block editor — arrive as a bare string
// where the workbench expects `[{ type: "text", text }]`.
describe("normalizeMessagesForLoad", () => {
  it("lifts string content into a text block", () => {
    expect(
      normalizeMessagesForLoad([
        { role: "system", content: "You are a helpful assistant" },
      ]),
    ).toEqual([
      {
        role: "system",
        content: [{ type: "text", text: "You are a helpful assistant" }],
      },
    ]);
  });

  it("leaves block content alone", () => {
    const blocks = [{ type: "text", text: "Summarize {{doc}}" }];
    expect(normalizeMessagesForLoad([{ role: "user", content: blocks }])).toEqual(
      [{ role: "user", content: blocks }],
    );
  });

  it("normalizes missing, null, and non-list content to an empty block list", () => {
    expect(
      normalizeMessagesForLoad([
        { role: "user" },
        { role: "user", content: null },
        { role: "user", content: "" },
        { role: "user", content: 42 },
      ]),
    ).toEqual([
      { role: "user", content: [] },
      { role: "user", content: [] },
      { role: "user", content: [] },
      { role: "user", content: [] },
    ]);
  });

  it("returns [] when messages is absent or not a list", () => {
    expect(normalizeMessagesForLoad(undefined)).toEqual([]);
    expect(normalizeMessagesForLoad(null)).toEqual([]);
    expect(normalizeMessagesForLoad({})).toEqual([]);
  });

  // Without this, the string reached Quill and the whole page unmounted.
  it("keeps string content out of the editor's block mapper", () => {
    const [message] = normalizeMessagesForLoad([
      { role: "user", content: "hi" },
    ]);
    expect(() => normalizeContentBlocks(message.content)).not.toThrow();
  });
});

// PromptEditor calls this in a useMemo on first render, so an unhandled shape
// throws during mount rather than degrading.
describe("normalizeContentBlocks", () => {
  it("lifts a raw string instead of throwing", () => {
    expect(normalizeContentBlocks("You are a helpful assistant")).toEqual([
      { type: "text", text: "You are a helpful assistant" },
    ]);
  });

  it("returns [] for a non-list, non-string shape", () => {
    expect(normalizeContentBlocks(42)).toEqual([]);
    expect(normalizeContentBlocks({ type: "text", text: "hi" })).toEqual([]);
  });

  it("passes falsy content through untouched", () => {
    expect(normalizeContentBlocks(null)).toBe(null);
    expect(normalizeContentBlocks(undefined)).toBe(undefined);
  });

  // A crash-free but blank editor is still broken: variables must survive.
  it("keeps a normalized string prompt readable to the content helpers", () => {
    const [message] = normalizeMessagesForLoad([
      { role: "user", content: "Summarize {{doc}} for {{audience}}" },
    ]);

    expect(isContentNotEmpty(message.content)).toBe(true);
    expect(extractVariables(message.content, "mustache")).toEqual([
      "doc",
      "audience",
    ]);
  });
});

describe("content helpers tolerate a text block with no text", () => {
  const contentArray = [{ type: "text" }];

  it("isContentNotEmpty does not throw", () => {
    expect(isContentNotEmpty(contentArray)).toBe(false);
  });

  it("extractVariables does not throw", () => {
    expect(extractVariables(contentArray, "mustache")).toEqual([]);
    expect(extractVariables(contentArray, "jinja")).toEqual([]);
  });
});

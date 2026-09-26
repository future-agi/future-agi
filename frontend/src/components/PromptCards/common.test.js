import { describe, expect, it } from "vitest";

import { getBlocks, getTextSelectionRange, normalizeContentBlocks } from "./common";

const mockQuill = (ops) => ({ getContents: () => ({ ops }) });

describe("normalizeContentBlocks", () => {
  it("converts old camelCase outer keys to snake_case", () => {
    const old = [
      {
        type: "imageUrl",
        imageUrl: { url: "https://img.com", imgName: "x", imgSize: 100 },
      },
      {
        type: "audioUrl",
        audioUrl: {
          url: "https://aud.io",
          audioName: "a",
          audioSize: 200,
          audioType: "mp3",
        },
      },
      {
        type: "pdfUrl",
        pdfUrl: { url: "https://pdf.dev", fileName: "doc", pdfSize: 300 },
      },
    ];
    const got = normalizeContentBlocks(old);
    expect(got[0]).toEqual({
      type: "image_url",
      image_url: { url: "https://img.com", img_name: "x", img_size: 100 },
    });
    expect(got[1]).toEqual({
      type: "audio_url",
      audio_url: {
        url: "https://aud.io",
        audio_name: "a",
        audio_size: 200,
        audio_type: "mp3",
      },
    });
    expect(got[2]).toEqual({
      type: "pdf_url",
      pdf_url: { url: "https://pdf.dev", file_name: "doc", pdf_size: 300 },
    });
  });

  it("leaves already-snake_case blocks unchanged", () => {
    const blocks = [
      {
        type: "image_url",
        image_url: { url: "https://img.com", img_name: "x" },
      },
      { type: "text", text: "hello" },
    ];
    expect(normalizeContentBlocks(blocks)).toEqual(blocks);
  });

  it("returns null/undefined as-is", () => {
    expect(normalizeContentBlocks(null)).toBeNull();
    expect(normalizeContentBlocks(undefined)).toBeUndefined();
  });
});

describe("getBlocks", () => {
  it("returns text blocks from string inserts", () => {
    const quill = mockQuill([{ insert: "Hello " }, { insert: "world" }]);
    expect(getBlocks(quill)).toEqual([{ type: "text", text: "Hello world" }]);
  });

  it("returns image block with snake_case inner keys", () => {
    const quill = mockQuill([
      {
        insert: {
          ImageBlot: {
            imageData: { url: "https://img.com", imgName: "x", imgSize: 100 },
          },
        },
      },
    ]);
    expect(getBlocks(quill)).toEqual([
      {
        type: "image_url",
        image_url: {
          url: "https://img.com",
          imgName: "x",
          imgSize: 100,
          img_name: "x",
          img_size: 100,
        },
      },
    ]);
  });

  it("returns audio block with snake_case inner keys", () => {
    const quill = mockQuill([
      {
        insert: {
          AudioBlot: {
            audioData: {
              url: "https://aud.io",
              audioName: "a",
              audioSize: 200,
              audioType: "mp3",
            },
          },
        },
      },
    ]);
    expect(getBlocks(quill)).toEqual([
      {
        type: "audio_url",
        audio_url: {
          url: "https://aud.io",
          audioName: "a",
          audioSize: 200,
          audioType: "mp3",
          audio_name: "a",
          audio_size: 200,
          audio_type: "mp3",
        },
      },
    ]);
  });

  it("returns pdf block with snake_case key and renamed field", () => {
    const quill = mockQuill([
      {
        insert: {
          PdfBlot: {
            pdfData: { url: "https://pdf.dev", pdf_name: "doc", pdfSize: 300 },
          },
        },
      },
    ]);
    expect(getBlocks(quill)).toEqual([
      {
        type: "pdf_url",
        pdf_url: {
          url: "https://pdf.dev",
          pdf_name: "doc",
          file_name: "doc",
          pdfSize: 300,
        },
      },
    ]);
  });

  it("separates text from surrounding media blocks", () => {
    const quill = mockQuill([
      { insert: "before" },
      {
        insert: {
          ImageBlot: {
            imageData: { url: "https://img.com", imgName: "x", imgSize: 100 },
          },
        },
      },
      { insert: "after" },
    ]);
    expect(getBlocks(quill)).toEqual([
      { type: "text", text: "before" },
      {
        type: "image_url",
        image_url: {
          url: "https://img.com",
          imgName: "x",
          imgSize: 100,
          img_name: "x",
          img_size: 100,
        },
      },
      { type: "text", text: "after" },
    ]);
  });

  it("handles EditVariable inserts", () => {
    const quill = mockQuill([
      { insert: { EditVariable: { fromBlock: true } } },
      { insert: { EditVariable: { fromBlock: false } } },
    ]);
    expect(getBlocks(quill)).toEqual([{ type: "text", text: "}" }]);
  });

  it("returns empty array for no ops", () => {
    expect(getBlocks(mockQuill([]))).toEqual([]);
  });
});

describe("getTextSelectionRange", () => {
  it("returns null when quill is null or undefined", () => {
    expect(getTextSelectionRange(null)).toBeNull();
    expect(getTextSelectionRange(undefined)).toBeNull();
  });

  it("returns full text range for text-only document", () => {
    const quill = mockQuill([{ insert: "Hello world\n" }]);
    expect(getTextSelectionRange(quill)).toEqual({ index: 0, length: 12 });
  });

  it("excludes media blot at the beginning of the document", () => {
    const quill = mockQuill([
      {
        insert: {
          ImageBlot: {
            imageData: { url: "https://img.com", imgName: "x", imgSize: 100 },
          },
        },
      },
      { insert: "Prompt text here\n" },
    ]);
    expect(getTextSelectionRange(quill)).toEqual({ index: 1, length: 17 });
  });

  it("excludes media blot at the end of the document", () => {
    const quill = mockQuill([
      { insert: "Prompt text here" },
      {
        insert: {
          PdfBlot: {
            pdfData: { url: "https://pdf.dev", pdf_name: "doc", pdfSize: 300 },
          },
        },
      },
      { insert: "\n" },
    ]);
    // Cursor in first text segment selects only first text segment
    expect(getTextSelectionRange(quill, { index: 5, length: 0 })).toEqual({
      index: 0,
      length: 16,
    });
  });

  it("selects active text segment when media blot is between text segments", () => {
    const quill = mockQuill([
      { insert: "Before text" },
      {
        insert: {
          AudioBlot: {
            audioData: { url: "https://aud.io", audioName: "a", audioSize: 200 },
          },
        },
      },
      { insert: "After text\n" },
    ]);
    // Cursor at index 2 (inside "Before text", which is range [0, 11))
    expect(getTextSelectionRange(quill, { index: 2, length: 0 })).toEqual({
      index: 0,
      length: 11,
    });
    // Cursor at index 15 (inside "After text\n", which is range [12, 23))
    expect(getTextSelectionRange(quill, { index: 15, length: 0 })).toEqual({
      index: 12,
      length: 11,
    });
  });

  it("excludes multiple consecutive media blots", () => {
    const quill = mockQuill([
      {
        insert: {
          ImageBlot: {
            imageData: { url: "https://img.com", imgName: "x", imgSize: 100 },
          },
        },
      },
      {
        insert: {
          AudioBlot: {
            audioData: { url: "https://aud.io", audioName: "a", audioSize: 200 },
          },
        },
      },
      { insert: "Target prompt content\n" },
    ]);
    expect(getTextSelectionRange(quill)).toEqual({ index: 2, length: 22 });
  });

  it("includes inline EditVariable embeds inside text range and excludes media embeds", () => {
    const quill = mockQuill([
      {
        insert: {
          ImageBlot: {
            imageData: { url: "https://img.com", imgName: "x", imgSize: 100 },
          },
        },
      },
      { insert: "Hello {{" },
      { insert: { EditVariable: { fromBlock: false } } },
      { insert: "var}} world\n" },
    ]);
    // Op 0 is ImageBlot (len 1, range [0, 1)).
    // Op 1 ("Hello {{", len 8), Op 2 (EditVariable, len 1), Op 3 ("var}} world\n", len 12) form text segment [1, 22) of length 21.
    expect(getTextSelectionRange(quill)).toEqual({ index: 1, length: 21 });
  });

  it("returns null when document contains only media blots and no text", () => {
    const quill = mockQuill([
      {
        insert: {
          ImageBlot: {
            imageData: { url: "https://img.com", imgName: "x", imgSize: 100 },
          },
        },
      },
      {
        insert: {
          AudioBlot: {
            audioData: { url: "https://aud.io", audioName: "a", audioSize: 200 },
          },
        },
      },
    ]);
    expect(getTextSelectionRange(quill)).toBeNull();
  });
});


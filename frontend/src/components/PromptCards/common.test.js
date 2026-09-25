import { describe, expect, it } from "vitest";

import { getBlocks, normalizeContentBlocks } from "./common";

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

  it("keeps the image blot's snake_case metadata on save", () => {
    // ImageBlot.value() stores { url, img_name, img_size } in data-image-data,
    // so imageData carries snake_case keys — there is no imgName/imgSize.
    const quill = mockQuill([
      {
        insert: {
          ImageBlot: {
            imageData: { url: "https://img.com", img_name: "x", img_size: 100 },
          },
        },
      },
    ]);
    const [block] = getBlocks(quill);
    expect(block).toEqual({
      type: "image_url",
      image_url: { url: "https://img.com", img_name: "x", img_size: 100 },
    });
    // After JSON serialization (what the save path sends) the block must still
    // carry exactly the keys PromptEditor rebuilds the card from on reload.
    expect(Object.keys(JSON.parse(JSON.stringify(block.image_url)))).toEqual([
      "url",
      "img_name",
      "img_size",
    ]);
  });

  it("keeps the audio blot's snake_case metadata on save", () => {
    // AudioBlot.value() stores snake_case keys in data-audio-data.
    const quill = mockQuill([
      {
        insert: {
          AudioBlot: {
            audioData: {
              url: "https://aud.io",
              audio_name: "a",
              audio_size: 200,
              audio_type: "mp3",
            },
          },
        },
      },
    ]);
    const [block] = getBlocks(quill);
    expect(block).toEqual({
      type: "audio_url",
      audio_url: {
        url: "https://aud.io",
        audio_name: "a",
        audio_size: 200,
        audio_type: "mp3",
      },
    });
    expect(Object.keys(JSON.parse(JSON.stringify(block.audio_url)))).toEqual([
      "url",
      "audio_name",
      "audio_size",
      "audio_type",
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
            imageData: { url: "https://img.com", img_name: "x", img_size: 100 },
          },
        },
      },
      { insert: "after" },
    ]);
    expect(getBlocks(quill)).toEqual([
      { type: "text", text: "before" },
      {
        type: "image_url",
        image_url: { url: "https://img.com", img_name: "x", img_size: 100 },
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

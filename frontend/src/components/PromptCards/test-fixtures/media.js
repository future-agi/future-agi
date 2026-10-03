// Synthetic fixtures for TH-150 prompt-editor media tests. Never customer content.
// jsdom never fetches these URLs; they are opaque strings for component tests.

export const PNG_1x1 = {
  kind: "image",
  url: "https://fixtures.invalid/th150/pixel.png",
  name: "pixel.png",
  size: 68,
};

export const WAV_1S = {
  kind: "audio",
  url: "https://fixtures.invalid/th150/silence-1s.wav",
  name: "silence-1s.wav",
  size: 88244,
  mimeType: "audio/wav",
};

export const PDF_1P = {
  kind: "pdf",
  url: "https://fixtures.invalid/th150/one-page.pdf",
  name: "one-page.pdf",
  size: 1024,
};

const noop = () => {};

// Delta insert ops in the flat create() shape PromptEditor uses today.
export const imageOp = (overrides = {}) => ({
  insert: {
    ImageBlot: {
      url: PNG_1x1.url,
      name: PNG_1x1.name,
      size: PNG_1x1.size,
      id: "img-fixture",
      setSelectedImage: noop,
      handleRemoveImage: noop,
      ...overrides,
    },
  },
});

export const audioOp = (overrides = {}) => ({
  insert: {
    AudioBlot: {
      url: WAV_1S.url,
      name: WAV_1S.name,
      size: WAV_1S.size,
      mimeType: WAV_1S.mimeType,
      id: "audio-fixture",
      handleRemoveAudio: noop,
      ...overrides,
    },
  },
});

export const pdfOp = (overrides = {}) => ({
  insert: {
    PdfBlot: {
      url: PDF_1P.url,
      name: PDF_1P.name,
      size: PDF_1P.size,
      id: "pdf-fixture",
      handleRemovePdf: noop,
      ...overrides,
    },
  },
});

// Prompt blocks (the `prompt` prop shape) for the same documents.
export const imageBlock = {
  type: "image_url",
  image_url: {
    url: PNG_1x1.url,
    img_name: PNG_1x1.name,
    img_size: PNG_1x1.size,
  },
};
export const audioBlock = {
  type: "audio_url",
  audio_url: {
    url: WAV_1S.url,
    audio_name: WAV_1S.name,
    audio_size: WAV_1S.size,
    audio_type: WAV_1S.mimeType,
  },
};
export const pdfBlock = {
  type: "pdf_url",
  pdf_url: { url: PDF_1P.url, file_name: PDF_1P.name, pdf_size: PDF_1P.size },
};
export const textBlock = (text) => ({ type: "text", text });
// What getBlocks emits for the PDF block today: the stored pdfData spread plus
// file_name (common.js keeps pdf_name alongside; unchanged by TH-150).
export const pdfBlockEmitted = {
  type: "pdf_url",
  pdf_url: {
    url: PDF_1P.url,
    pdf_name: PDF_1P.name,
    pdf_size: PDF_1P.size,
    file_name: PDF_1P.name,
  },
};

// Explicit Quill indices (Quill gives every block its own line, so a text block
// before an embed always ends with a structural "\n"):
//   DOC_TEXT_PDF_TEXT: h0 e1 l2 l3 o4 \n5 [pdf]6 w7 o8 r9 l10 d11 \n12  -> length 13
export const DOC_TEXT_PDF_TEXT = [
  textBlock("hello"),
  pdfBlock,
  textBlock("world"),
];
//   DOC_HELLO_IMG_WORLD: hello\n 0..5, [img]6, world\n 7..12 -> length 13
export const DOC_HELLO_IMG_WORLD = [
  textBlock("hello"),
  imageBlock,
  textBlock("world"),
];
//   DOC_IMG_AUDIO: a0 \n1 [img]2 [audio]3 b4 \n5 -> length 6
export const DOC_IMG_AUDIO = [
  textBlock("a"),
  imageBlock,
  audioBlock,
  textBlock("b"),
];
//   DOC_PDF_ONLY: [pdf]0 \n1 -> length 2
export const DOC_PDF_ONLY = [pdfBlock];

export const PROVENANCE_O1 = {
  userId: "user-1",
  orgId: "org-O1",
  workspaceId: null,
};
export const PROVENANCE_O2 = { ...PROVENANCE_O1, orgId: "org-O2" };

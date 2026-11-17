import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { safePdfDocumentOptions } from "../pdf-document-options.mjs";

const mocks = vi.hoisted(() => ({
  document: vi.fn(),
  worker: {},
  closePreview: vi.fn(),
  fileUrl: "https://documents.example.test/report.pdf",
}));
vi.mock("react-pdf", () => ({
  Document: (props) => {
    mocks.document(props);
    return null;
  },
  Page: () => null,
  pdfjs: { GlobalWorkerOptions: mocks.worker },
}));
vi.mock("src/utils/CommonStores/pdfPreviewStore", () => ({
  usePdfPreviewStoreShallow: (selector) =>
    selector({
      openPdfPreviewDrawer: {
        url: mocks.fileUrl,
        name: "report.pdf",
        type: "pdf",
      },
      closePreview: mocks.closePreview,
    }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/loading-screen", () => ({ LoadingScreen: () => null }));
vi.mock("src/components/external-content/ExternalContentFrame", () => ({
  default: () => null,
}));
vi.mock("src/sections/knowledge-base/sheet-view/icons", () => ({
  getFileIcon: () => "",
}));
vi.mock("src/components/common", () => ({ errorMessages: {} }));
vi.mock("src/utils/logger", () => ({ default: { error: vi.fn() } }));

import PdfPreviewDrawer from "../PdfPreviewDrawer";

beforeEach(() => {
  mocks.document.mockClear();
  mocks.closePreview.mockClear();
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Unexpected fetch in PDF drawer containment test");
    }),
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PdfPreviewDrawer CVE-2024-4367 containment", () => {
  it("renders the document with PDF.js evaluation off and a same-origin worker", async () => {
    render(<PdfPreviewDrawer />);
    await waitFor(() => expect(mocks.document).toHaveBeenCalled());
    const props = mocks.document.mock.calls.at(-1)[0];
    expect(props.file).toBe(mocks.fileUrl);
    expect(props.options.isEvalSupported).toBe(false);
    expect(String(mocks.worker.workerSrc)).not.toMatch(/^https?:\/\//);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("disables font eval without discarding or mutating document parameters", () => {
    for (const evalOption of [
      {},
      { isEvalSupported: true },
      { isEvalSupported: false },
    ]) {
      const otherParams = {
        url: mocks.fileUrl,
        data: new Uint8Array([37, 80, 68, 70]),
        cMapUrl: "/pdf-cmaps/",
        cMapPacked: true,
        withCredentials: true,
        disableRange: true,
        rangeChunkSize: 32768,
      };
      const input = Object.freeze({ ...otherParams, ...evalOption });
      const result = safePdfDocumentOptions(input);
      expect(result).toEqual({ ...otherParams, isEvalSupported: false });
      expect(result).not.toBe(input);
      expect(result.data).toBe(input.data);
      expect(input).toEqual({ ...otherParams, ...evalOption });
    }
  });
});

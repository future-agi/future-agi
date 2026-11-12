import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import PdfPreviewDrawer from "../PdfPreviewDrawer";

const mocks = vi.hoisted(() => ({
  viewer: vi.fn(),
  closePreview: vi.fn(),
  fileUrl: "https://documents.example.test/report.pdf",
}));
vi.mock("@react-pdf-viewer/core", () => ({
  Viewer: (props) => {
    mocks.viewer(props);
    return null;
  },
  Worker: ({ children }) => children,
}));
vi.mock("@react-pdf-viewer/default-layout", () => ({
  defaultLayoutPlugin: () => ({}),
}));
vi.mock("pdfjs-dist/build/pdf.worker.min.js?url", () => ({
  default: "/mock-pdf-worker.js",
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

beforeEach(() => {
  mocks.viewer.mockClear();
  mocks.closePreview.mockClear();
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Unexpected fetch in PDF drawer containment test");
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PdfPreviewDrawer CVE-2024-4367 containment", () => {
  it("disables font eval without discarding or mutating document parameters", async () => {
    render(<PdfPreviewDrawer />);
    await waitFor(() => expect(mocks.viewer).toHaveBeenCalled());
    const props = mocks.viewer.mock.calls.at(-1)[0];
    expect(props.fileUrl).toBe(mocks.fileUrl);
    expect(props.transformGetDocumentParams).toEqual(expect.any(Function));

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
      const result = props.transformGetDocumentParams(input);
      expect(result).toEqual({ ...otherParams, isEvalSupported: false });
      expect(result).not.toBe(input);
      expect(result.data).toBe(input.data);
      expect(input).toEqual({ ...otherParams, ...evalOption });
    }
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});

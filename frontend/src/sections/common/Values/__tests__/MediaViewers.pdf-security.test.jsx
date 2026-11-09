import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { PdfPreview } from "../MediaViewers";

const pdfjs = vi.hoisted(() => ({ getDocument: vi.fn() }));
vi.mock("pdfjs-dist", () => ({
  getDocument: pdfjs.getDocument,
  GlobalWorkerOptions: {},
}));
vi.mock("pdfjs-dist/build/pdf.worker.min.js?url", () => ({
  default: "/mock-pdf-worker.js",
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));

beforeEach(() => {
  pdfjs.getDocument.mockReset();
  // No URL in these tests may reach the network, including the size HEAD.
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: false, headers: new Headers() }),
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("PdfPreview CVE-2024-4367 containment", () => {
  it.each([
    "https://documents.example.test/report.pdf?download=1",
    "data:application/pdf;base64,JVBERi0xLjcK",
  ])("disables font eval while retaining the document URL: %s", async (url) => {
    const renderPage = vi.fn().mockReturnValue({ promise: Promise.resolve() });
    const getPage = vi.fn().mockResolvedValue({
      getViewport: ({ scale }) => ({ width: 360 * scale, height: 480 * scale }),
      render: renderPage,
    });
    const destroy = vi.fn();
    pdfjs.getDocument.mockReturnValue({
      promise: Promise.resolve({ numPages: 2, getPage }),
      destroy,
    });

    const { container, unmount } = render(<PdfPreview url={url} />);

    await waitFor(() => expect(pdfjs.getDocument).toHaveBeenCalledTimes(1));
    expect(pdfjs.getDocument).toHaveBeenCalledWith({
      url,
      isEvalSupported: false,
    });
    await waitFor(() => expect(renderPage).toHaveBeenCalledTimes(1));
    expect(getPage).toHaveBeenCalledWith(1);
    expect(container.querySelector("canvas")).toHaveAttribute("width", "720");
    expect(container.querySelector("canvas")).toHaveAttribute("height", "960");
    expect(screen.getByText(/2 pages/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open/ })).toHaveAttribute(
      "href",
      url,
    );
    expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute(
      "href",
      url,
    );

    unmount();
    expect(destroy).toHaveBeenCalledTimes(1);
  });
});

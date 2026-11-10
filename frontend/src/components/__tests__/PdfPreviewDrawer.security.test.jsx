import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import localWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { safePdfDocumentOptions } from "../pdf-document-options.mjs";

const { viewer, worker, preview, page } = vi.hoisted(() => ({
  viewer: { props: null },
  worker: {},
  preview: { url: "blob:https://example.invalid/owned-document" },
  page: { props: null },
}));

vi.mock("react-pdf", async () => {
  const { default: PropTypes } = await import("prop-types");
  const Document = (props) => {
    viewer.props = props;
    return <div data-testid="pdf-rendered">PDF preview{props.children}</div>;
  };
  Document.propTypes = { children: PropTypes.node };
  const Page = (props) => {
    page.props = props;
    return <div>Rendered page {props.pageNumber}</div>;
  };
  Page.propTypes = { pageNumber: PropTypes.number };
  return { pdfjs: { GlobalWorkerOptions: worker }, Document, Page };
});
vi.mock("src/utils/CommonStores/pdfPreviewStore", () => ({
  usePdfPreviewStoreShallow: (selector) =>
    selector({
      openPdfPreviewDrawer: {
        url: preview.url,
        name: "ordinary.pdf",
        type: "pdf",
      },
      closePreview: () => {},
    }),
}));
vi.mock("src/components/loading-screen", () => ({ LoadingScreen: () => null }));
vi.mock("src/utils/logger", () => ({ default: { error: vi.fn() } }));
vi.mock("src/sections/knowledge-base/sheet-view/icons", () => ({
  getFileIcon: () => "/icon.svg",
}));
vi.mock("../iconify", () => ({ default: () => null }));
vi.mock("../svg-color", () => ({ default: () => null }));
vi.mock("../common", () => ({
  errorMessages: {
    notAccessible: "The file is not accessible or has been moved.",
  },
}));

import PdfPreviewDrawer from "../PdfPreviewDrawer";

beforeEach(() => {
  preview.url = "blob:https://example.invalid/owned-document";
  viewer.props = null;
  page.props = null;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});

describe("PDF preview document boundary", () => {
  it("retains the legitimate preview and its matching local worker", () => {
    render(<PdfPreviewDrawer />);
    expect(screen.getByTestId("pdf-rendered")).toHaveTextContent("PDF preview");
    expect(viewer.props.file).toBe(preview.url);
    expect(worker.workerSrc).toBe(localWorkerUrl);
    expect(worker.workerSrc).not.toMatch(/^https?:\/\//);
  });

  it("forces PDF.js evaluation off without discarding normal document options", () => {
    render(<PdfPreviewDrawer />);
    expect(viewer.props.options.isEvalSupported).toBe(false);
    const original = {
      url: preview.url,
      cMapUrl: "/cmaps/",
      isEvalSupported: true,
    };
    const options = safePdfDocumentOptions(original);
    expect(options).toEqual({ ...original, isEvalSupported: false });
    expect(original.isEvalSupported).toBe(true);
  });

  it("renders pages and retains bounded, accessible navigation", () => {
    render(<PdfPreviewDrawer />);
    act(() => viewer.props.onLoadSuccess({ numPages: 2 }));
    expect(screen.getByText("Page 1 of 2")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Previous page" }),
    ).toBeDisabled();
    expect(page.props.pageNumber).toBe(1);
    expect(page.props.renderTextLayer).toBe(true);
    expect(page.props.renderAnnotationLayer).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText("Page 2 of 2")).toBeVisible();
    expect(page.props.pageNumber).toBe(2);
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(page.props.pageNumber).toBe(1);
  });

  it("reports unreadable documents without rendering a stale page", () => {
    render(<PdfPreviewDrawer />);
    act(() => viewer.props.onLoadError(new Error("invalid PDF")));
    expect(screen.getByText("Unable to preview this document")).toBeVisible();
    expect(page.props).toBeNull();
  });

  it("fails closed when the document URL is missing", () => {
    preview.url = undefined;
    render(<PdfPreviewDrawer />);
    expect(screen.getByText("Unable to preview this document")).toBeVisible();
    expect(viewer.props).toBeNull();
  });
});

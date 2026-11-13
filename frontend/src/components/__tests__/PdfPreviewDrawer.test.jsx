import React from "react";
import { act, fireEvent, render, screen } from "src/utils/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import localWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import openjpegWasmUrl from "pdfjs-dist/wasm/openjpeg.wasm?url";
import openjpegFallbackUrl from "pdfjs-dist/wasm/openjpeg_nowasm_fallback.js?url";
import { safePdfDocumentOptions } from "../pdf-document-options.mjs";

const { viewer, worker, preview, page } = vi.hoisted(() => ({
  viewer: { props: null, onItemClick: null },
  worker: {},
  preview: { url: "blob:https://example.invalid/owned-document" },
  page: { props: null },
}));

vi.mock("react-pdf", async () => {
  const { default: PropTypes } = await import("prop-types");
  const Document = (props) => {
    viewer.props = props;
    // react-pdf's link service keeps the onItemClick of the first render.
    viewer.onItemClick ??= props.onItemClick;
    return <div data-testid="pdf-rendered">PDF preview{props.children}</div>;
  };
  Document.propTypes = {
    children: PropTypes.node,
    onItemClick: PropTypes.func,
  };
  const Page = (props) => {
    page.props = props;
    return <div ref={props.inputRef}>Rendered page {props.pageNumber}</div>;
  };
  Page.propTypes = {
    pageNumber: PropTypes.number,
    inputRef: PropTypes.oneOfType([PropTypes.func, PropTypes.object]),
  };
  return { pdfjs: { GlobalWorkerOptions: worker }, Document, Page };
});
vi.mock("src/utils/CommonStores/pdfPreviewStore", () => ({
  usePdfPreviewStoreShallow: (selector) =>
    selector({
      openPdfPreviewDrawer: preview.open
        ? {
            url: preview.url,
            name: "ordinary.pdf",
            type: preview.type,
          }
        : null,
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
  preview.open = true;
  preview.type = "pdf";
  viewer.props = null;
  viewer.onItemClick = null;
  page.props = null;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});

describe("PDF preview rendering", () => {
  it("renders the document with its matching local worker", () => {
    render(<PdfPreviewDrawer />);
    expect(screen.getByTestId("pdf-rendered")).toHaveTextContent("PDF preview");
    expect(viewer.props.file).toBe(preview.url);
    expect(worker.workerSrc).toBe(localWorkerUrl);
    expect(worker.workerSrc).not.toMatch(/^https?:\/\//);
  });

  it("loads the JPEG 2000 image decoder from the application origin", () => {
    render(<PdfPreviewDrawer />);
    const { wasmUrl } = viewer.props.options;
    expect(wasmUrl).toEqual(expect.any(String));
    // PDF.js requires a directory URL and loads each decoder file by name from it.
    expect(wasmUrl).toMatch(/\/$/);
    expect(new URL(wasmUrl).origin).toBe(window.location.origin);
    for (const [name, url] of [
      ["openjpeg.wasm", openjpegWasmUrl],
      ["openjpeg_nowasm_fallback.js", openjpegFallbackUrl],
    ]) {
      expect(new URL(name, wasmUrl).href).toBe(
        new URL(url, window.location.href).href,
      );
    }
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
    expect(page.props.renderAnnotationLayer).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText("Page 2 of 2")).toBeVisible();
    expect(page.props.pageNumber).toBe(2);
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(page.props.pageNumber).toBe(1);
  });

  it("follows internal PDF links to their page", () => {
    render(<PdfPreviewDrawer />);
    act(() => viewer.props.onLoadSuccess({ numPages: 3 }));
    expect(page.props.renderAnnotationLayer).toBe(true);
    act(() => viewer.onItemClick({ dest: [], pageIndex: 2, pageNumber: 3 }));
    expect(screen.getByText("Page 3 of 3")).toBeVisible();
    expect(page.props.pageNumber).toBe(3);
    // A destination outside the document keeps the current page.
    act(() => viewer.onItemClick({ dest: [], pageIndex: 8, pageNumber: 9 }));
    expect(page.props.pageNumber).toBe(3);
    act(() => viewer.onItemClick({ dest: [], pageIndex: 0, pageNumber: 1 }));
    expect(screen.getByText("Page 1 of 3")).toBeVisible();
  });

  describe("internal links to a position on a page", () => {
    // A 612x792 pt page drawn 1.5x wide, 48px below the top of the scrolled
    // preview (under the page buttons). PDF y coordinates run up from the
    // bottom of the page, so a point y pt up sits (792 - y) * 1.5 px down.
    const PAGE_OFFSET = 48;
    const SCALE = 1.5;
    const pageRef = { num: 6, gen: 0 };
    // PDF.js viewports: unrotated, and turned 90 degrees clockwise (where
    // the view runs down the page's x axis).
    const renderedPage = (pageNumber, rotation = 0) => {
      const [w, h] = rotation === 90 ? [792, 612] : [612, 792];
      return {
        pageNumber,
        width: w * SCALE,
        originalWidth: w,
        getViewport: ({ scale }) => ({
          viewBox: [0, 0, 612, 792],
          height: h * scale,
          convertToViewportPoint: (x, y) =>
            rotation === 90
              ? [y * scale, x * scale]
              : [x * scale, (792 - y) * scale],
        }),
      };
    };
    const below = (y) => PAGE_OFFSET + (792 - y) * SCALE;

    const openDocument = (numPages) => {
      render(<PdfPreviewDrawer />);
      act(() => viewer.props.onLoadSuccess({ numPages }));
      const scroller = screen.getByTestId("pdf-rendered").parentElement;
      // jsdom has no layout: give the preview a scroll position and place
      // the page element below the page buttons.
      let scrollTop = 0;
      Object.defineProperty(scroller, "scrollTop", {
        configurable: true,
        get: () => scrollTop,
        set: (value) => {
          scrollTop = value;
        },
      });
      scroller.getBoundingClientRect = () => ({ top: 100 });
      const paint = (pageNumber, rotation) => {
        const element = screen.getByText(`Rendered page ${pageNumber}`);
        element.getBoundingClientRect = () => ({
          top: 100 + PAGE_OFFSET - scrollTop,
        });
        act(() =>
          page.props.onRenderSuccess?.(renderedPage(pageNumber, rotation)),
        );
      };
      paint(1);
      return { scroller, paint };
    };

    it("scrolls to an /XYZ destination on the current page", () => {
      const { scroller } = openDocument(2);
      act(() =>
        viewer.onItemClick({
          dest: [pageRef, { name: "XYZ" }, 72, 300, null],
          pageIndex: 0,
          pageNumber: 1,
        }),
      );
      expect(page.props.pageNumber).toBe(1);
      expect(scroller.scrollTop).toBe(below(300));
    });

    it("opens an /XYZ destination on another page at its position", () => {
      const { scroller, paint } = openDocument(2);
      scroller.scrollTop = 40;
      act(() =>
        viewer.onItemClick({
          dest: [pageRef, { name: "XYZ" }, 0, 500, 0],
          pageIndex: 1,
          pageNumber: 2,
        }),
      );
      expect(screen.getByText("Page 2 of 2")).toBeVisible();
      paint(2);
      expect(scroller.scrollTop).toBe(below(500));
      // Repainting the same page (for example on resize) keeps the reader's place.
      scroller.scrollTop = 25;
      paint(2);
      expect(scroller.scrollTop).toBe(25);
    });

    it("uses the top of /FitH, /FitBH and /FitR destinations", () => {
      const { scroller } = openDocument(1);
      for (const [dest, top] of [
        [[pageRef, { name: "FitH" }, 600], 600],
        [[pageRef, { name: "FitBH" }, 450], 450],
        [[pageRef, { name: "FitR" }, 72, 100, 300, 250], 250],
      ]) {
        act(() => viewer.onItemClick({ dest, pageIndex: 0, pageNumber: 1 }));
        expect(scroller.scrollTop).toBe(below(top));
      }
    });

    it("shows the page buttons and the top of the page for whole-page destinations", () => {
      const { scroller, paint } = openDocument(3);
      for (const [dest, pageNumber] of [
        [[pageRef, { name: "Fit" }], 2],
        [[pageRef, { name: "FitV" }, 72], 3],
        [[pageRef, { name: "XYZ" }, null, null, null], 1],
        [[pageRef, { name: "FitH" }, null], 2],
      ]) {
        scroller.scrollTop = 500;
        act(() =>
          viewer.onItemClick({ dest, pageIndex: pageNumber - 1, pageNumber }),
        );
        paint(pageNumber);
        expect(page.props.pageNumber).toBe(pageNumber);
        expect(scroller.scrollTop).toBe(0);
        expect(screen.getByText(`Page ${pageNumber} of 3`)).toBeVisible();
      }
    });

    it("keeps the scroll position for a destination without a vertical position on the current page", () => {
      const { scroller } = openDocument(1);
      for (const dest of [
        [pageRef, { name: "XYZ" }, 72, null, null],
        [pageRef, { name: "FitH" }, null],
        [pageRef, { name: "FitV" }, 72],
      ]) {
        scroller.scrollTop = 500;
        act(() => viewer.onItemClick({ dest, pageIndex: 0, pageNumber: 1 }));
        expect(scroller.scrollTop).toBe(500);
      }
    });

    it("scrolls to the destination's position on a rotated page", () => {
      const { scroller, paint } = openDocument(2);
      act(() =>
        viewer.onItemClick({
          dest: [pageRef, { name: "XYZ" }, 300, 500, null],
          pageIndex: 1,
          pageNumber: 2,
        }),
      );
      paint(2, 90);
      // Turned 90 degrees, x runs down the view.
      expect(scroller.scrollTop).toBe(PAGE_OFFSET + 300 * SCALE);
      for (const [dest, top] of [
        [[pageRef, { name: "FitV" }, 200], 200],
        [[pageRef, { name: "FitR" }, 100, 50, 400, 250], 100],
      ]) {
        act(() => viewer.onItemClick({ dest, pageIndex: 1, pageNumber: 2 }));
        expect(scroller.scrollTop).toBe(PAGE_OFFSET + top * SCALE);
      }
      // A top alone does not place a point vertically on this page.
      scroller.scrollTop = 70;
      act(() =>
        viewer.onItemClick({
          dest: [pageRef, { name: "FitH" }, 600],
          pageIndex: 1,
          pageNumber: 2,
        }),
      );
      expect(scroller.scrollTop).toBe(70);
    });

    it("does not scroll when the page buttons change page", () => {
      const { scroller, paint } = openDocument(2);
      act(() =>
        viewer.onItemClick({
          dest: [pageRef, { name: "XYZ" }, 0, 500, null],
          pageIndex: 1,
          pageNumber: 2,
        }),
      );
      // The reader moves on before the linked page has painted.
      fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
      scroller.scrollTop = 30;
      paint(1);
      expect(page.props.pageNumber).toBe(1);
      expect(scroller.scrollTop).toBe(30);
    });
  });

  it("keeps link annotations and opens external links in a new tab", () => {
    render(<PdfPreviewDrawer />);
    act(() => viewer.props.onLoadSuccess({ numPages: 1 }));
    expect(viewer.props.externalLinkTarget).toBe("_blank");
    expect(viewer.props.externalLinkRel.split(" ")).toEqual(
      expect.arrayContaining(["noopener", "noreferrer"]),
    );
    const link = { subtype: "Link", url: "https://example.com/" };
    expect(
      page.props.filterAnnotations({
        annotations: [
          link,
          { subtype: "Widget" },
          { subtype: "FileAttachment" },
        ],
      }),
    ).toEqual([link]);
  });

  it("reports unreadable documents without rendering a stale page", () => {
    render(<PdfPreviewDrawer />);
    act(() => viewer.props.onLoadError(new Error("invalid PDF")));
    expect(screen.getByText("Unable to preview this document")).toBeVisible();
    expect(page.props).toBeNull();
  });

  it("reports a newly opened preview with no document metadata", () => {
    preview.open = false;
    preview.url = undefined;
    preview.type = undefined;
    const { rerender } = render(<PdfPreviewDrawer />);
    expect(screen.queryByText("Unable to preview this document")).toBeNull();
    preview.open = true;
    rerender(<PdfPreviewDrawer />);
    expect(screen.getByText("Unable to preview this document")).toBeVisible();
    expect(viewer.props).toBeNull();
  });

  it("shows an error without rendering when the document URL is missing", () => {
    preview.url = undefined;
    render(<PdfPreviewDrawer />);
    expect(screen.getByText("Unable to preview this document")).toBeVisible();
    expect(viewer.props).toBeNull();
  });
});

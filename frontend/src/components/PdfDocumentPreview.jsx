import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { Document, Page, pdfjs } from "react-pdf";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import openjpegWasmUrl from "pdfjs-dist/wasm/openjpeg.wasm?url";
// PDF.js imports this by name from the same directory when WebAssembly is
// unavailable; vite.config.js emits both decoder files there unhashed.
import "pdfjs-dist/wasm/openjpeg_nowasm_fallback.js?url";
import { safePdfDocumentOptions } from "./pdf-document-options.mjs";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

// Keep the worker and renderer on the same pinned PDF.js version.
// Disable PDF.js evaluation for this preview.
pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
const documentOptions = safePdfDocumentOptions({
  useSystemFonts: true,
  // JPEG 2000 images (common in scanned PDFs) decode only with these files.
  wasmUrl: new URL(".", new URL(openjpegWasmUrl, window.location.href)).href,
});

// Keep the PDF's links clickable; other annotation types are not shown.
const keepLinkAnnotations = ({ annotations }) =>
  annotations.filter((annotation) => annotation.subtype === "Link");

// The page area a link destination points to, as PDF x and y ranges where a
// null range leaves that coordinate unchanged; null for the whole page.
// Zoom is not used: the preview always shows the full page width.
const destinationArea = (dest) => {
  const [, mode, ...args] = Array.isArray(dest) ? dest : [];
  const at = (value) => (Number.isFinite(value) ? value : null);
  const range = (low, high) =>
    at(low) === null || at(high) === null ? null : [low, high];
  switch (mode?.name) {
    case "XYZ":
      return { x: range(args[0], args[0]), y: range(args[1], args[1]) };
    case "FitH":
    case "FitBH":
      return { x: null, y: range(args[0], args[0]) };
    case "FitV":
    case "FitBV":
      return { x: range(args[0], args[0]), y: null };
    case "FitR":
      return { x: range(args[0], args[2]), y: range(args[1], args[3]) };
    default:
      return null;
  }
};

// The area's distance below the top of the page in the view, or null when it
// has no vertical position there (rotated pages run the view along PDF x).
const areaTopInView = (viewport, area) => {
  const [left, bottom, right, top] = viewport.viewBox;
  const xs = area.x ?? [left, right];
  const ys = area.y ?? [bottom, top];
  const viewYs = xs.flatMap((x) =>
    ys.map((y) => viewport.convertToViewportPoint(x, y)[1]),
  );
  const [first, last] = [Math.min(...viewYs), Math.max(...viewYs)];
  // Spanning the full page height leaves the vertical position unchanged.
  if (first <= 0.5 && last >= viewport.height - 0.5) return null;
  return Math.max(0, first);
};

export default function PdfDocumentPreview({ fileUrl, onLoad, onError }) {
  const container = useRef(null);
  const [width, setWidth] = useState(600);
  const [pages, setPages] = useState(0);
  // react-pdf keeps the onItemClick from the first render, so it reads the
  // page count from a ref rather than from state.
  const pageCount = useRef(0);
  const [pageNumber, setPageNumber] = useState(1);
  // The same applies to the link target and the painted page it scrolls to.
  const pendingLink = useRef(null);
  const paintedPage = useRef(null);
  const pageElement = useRef(null);

  // Scroll the preview so the pending link's destination is at the top.
  const scrollToPendingLink = () => {
    const link = pendingLink.current;
    const page = paintedPage.current;
    const scroller = container.current;
    const element = pageElement.current;
    if (!link || !page || !scroller || !element) return;
    if (page.pageNumber !== link.pageNumber) return;
    pendingLink.current = null;
    const area = destinationArea(link.dest);
    const scale = page.width / page.originalWidth;
    const offset = area && areaTopInView(page.getViewport({ scale }), area);
    if (offset === null) {
      // Without a vertical position, a link on the same page keeps the
      // reader's place; whole-page destinations and other pages show the page
      // buttons and the top of the page.
      if (!area || !link.samePage) scroller.scrollTop = 0;
      return;
    }
    const pageTop =
      element.getBoundingClientRect().top -
      scroller.getBoundingClientRect().top +
      scroller.scrollTop;
    scroller.scrollTop = pageTop + offset;
  };

  const showPage = (next) => {
    pendingLink.current = null;
    paintedPage.current = null;
    setPageNumber(next);
  };

  useEffect(() => {
    const element = container.current;
    if (!element) return undefined;
    const updateWidth = () => setWidth(Math.max(1, element.clientWidth));
    updateWidth();
    const observer = new ResizeObserver(updateWidth);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <Box ref={container} sx={{ height: "100%", overflow: "auto" }}>
      <Document
        file={fileUrl}
        options={documentOptions}
        onLoadSuccess={({ numPages }) => {
          pageCount.current = numPages;
          setPages(numPages);
          showPage(1);
          onLoad();
        }}
        onLoadError={onError}
        // Only the current page is rendered, so internal links (tables of
        // contents, cross references) move the preview to their page and
        // then scroll to the destination's position once it is painted.
        onItemClick={({ dest, pageNumber: target }) => {
          if (!(target >= 1 && target <= pageCount.current)) return;
          const samePage = paintedPage.current?.pageNumber === target;
          if (!samePage) showPage(target);
          pendingLink.current = { pageNumber: target, dest, samePage };
          scrollToPendingLink();
        }}
        externalLinkTarget="_blank"
        externalLinkRel="noopener noreferrer nofollow"
        loading={<Typography role="status">Loading PDF…</Typography>}
        error={<Typography role="alert">Failed to load Resource</Typography>}
      >
        {pages > 0 && (
          <>
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="center"
              spacing={2}
            >
              <Button
                disabled={pageNumber <= 1}
                onClick={() => showPage((n) => n - 1)}
              >
                Previous page
              </Button>
              <Typography aria-live="polite">
                Page {pageNumber} of {pages}
              </Typography>
              <Button
                disabled={pageNumber >= pages}
                onClick={() => showPage((n) => n + 1)}
              >
                Next page
              </Button>
            </Stack>
            <Page
              pageNumber={pageNumber}
              inputRef={pageElement}
              width={width}
              renderTextLayer
              renderAnnotationLayer
              filterAnnotations={keepLinkAnnotations}
              onRenderSuccess={(page) => {
                paintedPage.current = page;
                scrollToPendingLink();
              }}
              onRenderError={onError}
              loading={<Typography role="status">Rendering page…</Typography>}
            />
          </>
        )}
      </Document>
    </Box>
  );
}

PdfDocumentPreview.propTypes = {
  fileUrl: PropTypes.string.isRequired,
  onLoad: PropTypes.func.isRequired,
  onError: PropTypes.func.isRequired,
};

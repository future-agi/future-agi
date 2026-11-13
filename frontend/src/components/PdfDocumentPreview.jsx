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

// The PDF y coordinate a link destination puts at the top of the view, or
// null for the top of the page. Horizontal position and zoom are not used:
// the preview always shows the full page width.
const destinationTop = (dest) => {
  const [, mode, ...args] = Array.isArray(dest) ? dest : [];
  switch (mode?.name) {
    case "XYZ":
      return args[1];
    case "FitH":
    case "FitBH":
      return args[0];
    case "FitR":
      return args[3];
    default:
      return null;
  }
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
    const top = destinationTop(link.dest);
    if (typeof top !== "number" || !Number.isFinite(top)) {
      // Whole-page destinations show the page buttons and the top of the page.
      scroller.scrollTop = 0;
      return;
    }
    const pageTop =
      element.getBoundingClientRect().top -
      scroller.getBoundingClientRect().top +
      scroller.scrollTop;
    const scale = page.width / page.originalWidth;
    const [, offset] = page
      .getViewport({ scale })
      .convertToViewportPoint(0, top);
    scroller.scrollTop = pageTop + Math.max(0, offset);
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
          if (paintedPage.current?.pageNumber !== target) {
            showPage(target);
          }
          pendingLink.current = { pageNumber: target, dest };
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

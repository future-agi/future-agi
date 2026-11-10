import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { Document, Page, pdfjs } from "react-pdf";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { safePdfDocumentOptions } from "./pdf-document-options.mjs";
import "react-pdf/dist/Page/TextLayer.css";

// The worker and renderer must come from the same pinned, patched PDF.js build.
// Evaluation remains off as defense in depth, not as a dependency exception.
pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
const documentOptions = safePdfDocumentOptions({ useSystemFonts: true });

export default function PdfDocumentPreview({ fileUrl, onLoad, onError }) {
  const container = useRef(null);
  const [width, setWidth] = useState(600);
  const [pages, setPages] = useState(0);
  const [pageNumber, setPageNumber] = useState(1);

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
          setPages(numPages);
          setPageNumber(1);
          onLoad();
        }}
        onLoadError={onError}
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
                onClick={() => setPageNumber((n) => n - 1)}
              >
                Previous page
              </Button>
              <Typography aria-live="polite">
                Page {pageNumber} of {pages}
              </Typography>
              <Button
                disabled={pageNumber >= pages}
                onClick={() => setPageNumber((n) => n + 1)}
              >
                Next page
              </Button>
            </Stack>
            <Page
              pageNumber={pageNumber}
              width={width}
              renderTextLayer
              renderAnnotationLayer={false}
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

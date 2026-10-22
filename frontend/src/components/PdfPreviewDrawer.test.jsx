import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { usePdfPreviewStore } from "src/utils/CommonStores/pdfPreviewStore";
import PdfPreviewDrawer from "./PdfPreviewDrawer";

vi.mock("@react-pdf-viewer/core", () => ({
  Worker: ({ workerUrl, children }) => (
    <div data-testid="pdfjs-worker" data-worker-url={workerUrl}>
      {children}
    </div>
  ),
  Viewer: ({ fileUrl }) => (
    <div data-testid="pdfjs-viewer" data-file-url={fileUrl} />
  ),
}));

vi.mock("@react-pdf-viewer/default-layout", () => ({
  defaultLayoutPlugin: () => ({}),
}));

vi.mock("@mui/material", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    Drawer: ({ open, children }) => (open ? <div>{children}</div> : null),
  };
});

describe("PdfPreviewDrawer", () => {
  afterEach(() => {
    usePdfPreviewStore.setState({ openPdfPreviewDrawer: null });
  });

  it("loads the pdf.js worker from local pdfjs-dist instead of a CDN", () => {
    usePdfPreviewStore.setState({
      openPdfPreviewDrawer: {
        url: "/files/sample.pdf",
        name: "sample.pdf",
        type: "pdf",
        isPublic: true,
      },
    });

    render(<PdfPreviewDrawer />);

    const worker = screen.getByTestId("pdfjs-worker");
    const workerUrl = worker.getAttribute("data-worker-url") || "";

    expect(workerUrl).toBeTruthy();
    expect(workerUrl).toMatch(/pdf\.worker(\.min)?\.(js|mjs)/i);
    expect(workerUrl).not.toMatch(/unpkg|jsdelivr|cdnjs|cdn\./i);
    expect(screen.getByTestId("pdfjs-viewer")).toHaveAttribute(
      "data-file-url",
      "/files/sample.pdf",
    );
  });
});

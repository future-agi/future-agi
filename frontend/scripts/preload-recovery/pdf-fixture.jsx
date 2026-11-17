// Isolated frontend regression, not a product route or an authenticated walk.
// Uses the actual PdfPreview, pdf.js and React Router; no import/network mocks.
import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { PdfPreview } from "src/sections/common/Values/MediaViewers";

// A valid, blank one-page PDF. Compute xref offsets rather than depending on
// external fixtures, network access, fonts or a backend to exercise rendering.
function blankPdf() {
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources <<>> /Contents 4 0 R >>",
    "<< /Length 0 >>\nstream\nendstream",
  ];
  let pdf = "%PDF-1.4\n";
  const offsets = [0];
  objects.forEach((body, index) => {
    offsets.push(pdf.length);
    pdf += `${index + 1} 0 obj\n${body}\nendobj\n`;
  });
  const xref = pdf.length;
  pdf += `xref\n0 ${offsets.length}\n0000000000 65535 f \n`;
  offsets.slice(1).forEach((offset) => {
    pdf += `${String(offset).padStart(10, "0")} 00000 n \n`;
  });
  pdf += `trailer\n<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return `data:application/pdf;base64,${btoa(pdf)}`;
}

const pdf = blankPdf();
createRoot(document.getElementById("root")).render(
  <BrowserRouter basename="/preload-pdf">
    <nav>
      <Link to="/route-b">Leave preview</Link>
      <Link to="/pdf-a">Open preview</Link>
    </nav>
    <Routes>
      <Route path="/pdf-a" element={<PdfPreview url={pdf} width={200} />} />
      <Route path="/route-b" element={<h1>Route B remains visible</h1>} />
    </Routes>
  </BrowserRouter>,
);

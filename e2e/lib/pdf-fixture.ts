import type { Locator } from '@playwright/test';

/** Upload synthetic bytes through the product's own document input. */
export async function uploadPdfFixture(input: Locator, marker: string, unreadable = false) {
  const buffer = pdfFixture(marker, unreadable);
  await input.setInputFiles({ name: `${marker}.pdf`, mimeType: 'application/pdf', buffer },
    { timeout: 60_000 });
  return buffer;
}

/** Synthetic PDF bytes for the real document-upload input; no remote fixture. */
export function pdfFixture(marker: string, unreadable = false): Buffer {
  if (!/^[a-zA-Z0-9-]+$/.test(marker)) throw new Error('PDF marker must be alphanumeric');
  if (unreadable) return Buffer.from('%PDF-1.4\nunreadable synthetic document\n');
  const text = (page: number) => `BT /F1 20 Tf 72 700 Td (PDF ${page} ${marker}) Tj ET\n`;
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${Buffer.byteLength(text(1))} >>\nstream\n${text(1)}endstream`,
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 7 0 R >>',
    `<< /Length ${Buffer.byteLength(text(2))} >>\nstream\n${text(2)}endstream`,
  ];
  let content = '%PDF-1.4\n';
  const offsets = [0];
  for (const [index, object] of objects.entries()) {
    offsets.push(Buffer.byteLength(content));
    content += `${index + 1} 0 obj\n${object}\nendobj\n`;
  }
  const xref = Buffer.byteLength(content);
  content += `xref\n0 ${offsets.length}\n0000000000 65535 f \n`;
  for (const offset of offsets.slice(1)) content += `${String(offset).padStart(10, '0')} 00000 n \n`;
  content += `trailer\n<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(content);
}

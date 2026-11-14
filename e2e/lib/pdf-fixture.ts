import type { Locator } from '@playwright/test';

/** Upload synthetic bytes through the product's own document input. */
export async function uploadPdfFixture(input: Locator, marker: string, unreadable = false) {
  const buffer = pdfFixture(marker, unreadable);
  await input.setInputFiles({ name: `${marker}.pdf`, mimeType: 'application/pdf', buffer },
    { timeout: 60_000 });
  return buffer;
}

/** RGB of the JPEG 2000 swatch drawn on page 1; a renderer that cannot decode it leaves the area blank. */
export const JPX_SWATCH_RGB = [0, 102, 204] as const;
// 16x16 lossless JP2 file of JPX_SWATCH_RGB, written by Pillow 10.2 with OpenJPEG 2.5.0.
const JPX_SWATCH = Buffer.from(
  'AAAADGpQICANCocKAAAAFGZ0eXBqcDIgAAAAAGpwMiAAAAAtanAyaAAAABZpaGRyAAAAEAAAABAAAwcHAAAAAAAPY29scgEAAAAAABAAAACs'
  + 'anAyY/9P/1EALwAAAAAAEAAAABAAAAAAAAAAAAAAABAAAAAQAAAAAAAAAAAAAwcBAQcBAQcBAf9SAAwAAAABAAQEBAAB/1wAEEBASEhQSEhQ'
  + 'SEhQSEhQ/2QAJQABQ3JlYXRlZCBieSBPcGVuSlBFRyB2ZXJzaW9uIDIuNS4w/5AACgAAAAAAKAAB/5PfgAgHw+cECD/PtAgEF4CAgICAgICA'
  + 'gICAgP/Z', 'base64');

/** Labels drawn over page 1's link annotations. */
export const PDF_LINK_LABELS = {
  internal: 'Go to page 2', external: 'Example site',
  notes: 'Go to the notes below', otherNotes: 'Go to the page 2 notes',
} as const;
/** Annotation ids PDF.js gives page 1's links (object number + R), in drawing order. */
export const PDF_LINK_IDS = { internal: '9R', external: '10R', notes: '11R', otherNotes: '12R' } as const;
/**
 * Text drawn far down each page, where the /XYZ links on page 1 point. Both pages extend
 * below the usual letter page (MediaBox [0 -792 612 792]) so the preview can scroll each
 * note to the top of the view.
 */
export const pdfNotes = (page: 1 | 2, marker: string) => `Notes ${page} ${marker}`;
/** URI of page 1's external link; the preview opens it in a new tab and the flow never follows it. */
export const PDF_EXTERNAL_LINK = 'https://example.com/';

/** Synthetic PDF bytes for the real document-upload input; no remote fixture. */
export function pdfFixture(marker: string, unreadable = false): Buffer {
  if (!/^[a-zA-Z0-9-]+$/.test(marker)) throw new Error('PDF marker must be alphanumeric');
  if (unreadable) return Buffer.from('%PDF-1.4\nunreadable synthetic document\n');
  // Each page also draws its note well below the first screen: page 1's at y 80
  // (its link's /XYZ top is 100) and page 2's at y -100 (its link's top is -80).
  const text = (page: 1 | 2) => `BT /F1 20 Tf 72 700 Td (PDF ${page} ${marker}) Tj ET\n`
    + `BT /F1 20 Tf 72 ${page === 1 ? 80 : -100} Td (${pdfNotes(page, marker)}) Tj ET\n`;
  // Page 1 also paints the JPEG 2000 swatch into a 200x100 pt box below the text,
  // and labels the four link annotations listed after the image.
  const page1 = `${text(1)}BT /F1 14 Tf 72 660 Td (${PDF_LINK_LABELS.internal}) Tj ET\n`
    + `BT /F1 14 Tf 72 630 Td (${PDF_LINK_LABELS.external}) Tj ET\nq 200 0 0 100 72 500 cm /Im1 Do Q\n`
    + `BT /F1 14 Tf 72 470 Td (${PDF_LINK_LABELS.notes}) Tj ET\n`
    + `BT /F1 14 Tf 72 440 Td (${PDF_LINK_LABELS.otherNotes}) Tj ET\n`;
  // Built as latin1 so the binary image stream keeps its exact bytes and xref offsets.
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 -792 612 792] /Resources << /Font << /F1 4 0 R >> /XObject << /Im1 8 0 R >> >> /Contents 5 0 R /Annots [9 0 R 10 0 R 11 0 R 12 0 R] >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${page1.length} >>\nstream\n${page1}endstream`,
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 -792 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 7 0 R >>',
    `<< /Length ${text(2).length} >>\nstream\n${text(2)}endstream`,
    `<< /Type /XObject /Subtype /Image /Width 16 /Height 16 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /JPXDecode /Length ${JPX_SWATCH.length} >>\nstream\n${JPX_SWATCH.toString('latin1')}\nendstream`,
    // An internal link to page 2, as a table-of-contents entry is.
    '<< /Type /Annot /Subtype /Link /Rect [72 655 300 680] /Border [0 0 0] /Dest [6 0 R /Fit] >>',
    `<< /Type /Annot /Subtype /Link /Rect [72 625 300 650] /Border [0 0 0] /A << /S /URI /URI (${PDF_EXTERNAL_LINK}) >> >>`,
    // Section links to a position: one further down page 1, one far down page 2.
    '<< /Type /Annot /Subtype /Link /Rect [72 465 300 490] /Border [0 0 0] /Dest [3 0 R /XYZ 0 100 null] >>',
    '<< /Type /Annot /Subtype /Link /Rect [72 435 300 460] /Border [0 0 0] /Dest [6 0 R /XYZ 0 -80 null] >>',
  ];
  let content = '%PDF-1.4\n';
  const offsets = [0];
  for (const [index, object] of objects.entries()) {
    offsets.push(content.length);
    content += `${index + 1} 0 obj\n${object}\nendobj\n`;
  }
  const xref = content.length;
  content += `xref\n0 ${offsets.length}\n0000000000 65535 f \n`;
  for (const offset of offsets.slice(1)) content += `${String(offset).padStart(10, '0')} 00000 n \n`;
  content += `trailer\n<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(content, 'latin1');
}

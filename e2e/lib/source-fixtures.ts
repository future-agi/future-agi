import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
function textPdf(text: string): Buffer {
  if (!/^[\x20-\x7e]+$/.test(text)) throw new Error('PDF text must be printable ASCII');
  const escaped = text.replace(/\\/g, '\\\\').replace(/\(/g, '\\(').replace(/\)/g, '\\)');
  const stream = `BT /F1 12 Tf 72 720 Td (${escaped}) Tj ET`;
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
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

export const policyPdf = () => textPdf('The response must contain approved.');

export const customerCsv = Buffer.from(
  'customer_id,email,name\nC17,alice@example.com,Alice\nC18,bob@example.com,Bob\n',
);

export const validOrdersJson = Buffer.from(JSON.stringify([
  { order_id: 'O1', buyer_id: 'C17', amount_cents: 2500 },
  { order_id: 'O2', buyer_id: 'C18', amount_cents: 1200 },
]));

export const brokenOrdersJson = Buffer.from(JSON.stringify([
  { order_id: 'O3', buyer_id: 'C17', amount_cents: 500 },
  { order_id: 'O3', buyer_id: 'MISSING', amount_cents: 999 },
]));

export const evaluatorOnlyText = Buffer.from('EVALUATOR_ONLY_T20_SENTINEL');

export const rtfFixture = Buffer.from(
  String.raw`{\rtf1\ansi\deff0 {\fonttbl {\f0 Arial;}}\f0\fs24 RTF KB SENTINEL}`,
);

export function docxFixture(): Buffer {
  const dir = mkdtempSync(join(tmpdir(), 'e2e-t20-docx-'));
  try {
    mkdirSync(join(dir, '_rels'));
    mkdirSync(join(dir, 'word'));
    writeFileSync(join(dir, '[Content_Types].xml'), `<?xml version="1.0"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>`);
    writeFileSync(join(dir, '_rels/.rels'), `<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>`);
    writeFileSync(join(dir, 'word/document.xml'), `<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>DOCX KB SENTINEL</w:t></w:r></w:p></w:body></w:document>`);
    execFileSync('zip', ['-q', '-r', 'fixture.docx', '[Content_Types].xml', '_rels', 'word'], { cwd: dir });
    return readFileSync(join(dir, 'fixture.docx'));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

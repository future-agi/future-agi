#!/usr/bin/env node
// Writes the deterministic binary fixtures flows attach in the workbench
// editor (image / audio / pdf / csv upload flows). Run with
// `node scripts/gen-media-fixtures.mjs` from e2e/.
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { PNG_1X1, wavBuffer } from "../stack/mock-llm/server.mjs";

const E2E_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const MEDIA_DIR = path.join(E2E_DIR, "fixtures", "media");

// A minimal valid single-page PDF: header, one Catalog/Pages/Page object
// triple, an empty content stream, xref table, and trailer. Offsets are
// computed from the objects actually written, not hand-counted.
function buildPdf() {
  const objects = [
    "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
    "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
    "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> /Contents 4 0 R >>\nendobj\n",
    "4 0 obj\n<< /Length 0 >>\nstream\n\nendstream\nendobj\n",
  ];
  const header = "%PDF-1.4\n%\xE2\xE3\xCF\xD3\n";
  let body = header;
  const offsets = [0]; // object 0 is the free-list head, xref writes it separately
  for (const obj of objects) {
    offsets.push(Buffer.byteLength(body, "latin1"));
    body += obj;
  }
  const xrefStart = Buffer.byteLength(body, "latin1");
  let xref = `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (let i = 1; i <= objects.length; i++) {
    xref += `${String(offsets[i]).padStart(10, "0")} 00000 n \n`;
  }
  const trailer = `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefStart}\n%%EOF\n`;
  return Buffer.from(body + xref + trailer, "latin1");
}

function buildCsv() {
  return Buffer.from("topic\nkubernetes networking\nrust ownership\n", "utf8");
}

export function generate() {
  mkdirSync(MEDIA_DIR, { recursive: true });
  const files = {
    "pixel.png": PNG_1X1,
    "tone-1s.wav": wavBuffer(1),
    "sample.pdf": buildPdf(),
    "variables.csv": buildCsv(),
  };
  for (const [name, buf] of Object.entries(files)) {
    writeFileSync(path.join(MEDIA_DIR, name), buf);
  }
  return Object.fromEntries(
    Object.entries(files).map(([name, buf]) => [name, path.join(MEDIA_DIR, name)]),
  );
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const written = generate();
  for (const [name, p] of Object.entries(written)) console.log(`wrote ${p} (${name})`);
}

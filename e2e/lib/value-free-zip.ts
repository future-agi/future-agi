// HR-JRN-01: the zip codec and text test value-free mode reads and rewrites reports with
// (lib/value-free-codec.ts, lib/value-free-scrub.ts). It lives, unchanged, in
// lib/value-free-reading.mjs (plain Node), so the publication boundary's final check reads
// zips exactly as the scan does; this module keeps its importers' path.
export { asText, isZip, readZip, writeZip } from './value-free-reading.mjs';
export type { ZipEntry } from './value-free-reading.mjs';

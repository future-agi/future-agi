import { createHash } from 'node:crypto';
import { Agent as HttpAgent } from 'node:http';
import { Agent as HttpsAgent } from 'node:https';
import type { APIRequestContext, Locator } from '@playwright/test';
import { Client } from 'minio';
import { strToU8, zipSync } from 'fflate';
import { E2E } from './env';
import type { TestActor } from './provisioning';

// UploadFileSerializer / UploadFileView: image-only bootstrap initializes the real upload bucket.
const UPLOAD_IMAGE = '/model-hub/upload-file/';
const REQUEST_TIMEOUT = 60_000;
export type ImportFormat = 'csv' | 'xlsx';
export type MediaCase = 'blank' | 'valid' | 'invalid' | 'literal-nan' | 'literal-NA' | 'literal-null';
export interface MediaAsset { url: string; sha256: string; bytes: number }
export interface OwnedMedia { image: MediaAsset; audio: MediaAsset }
export const mediaDigest = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');

/** Fixed 96x64 RGB PNG, color (26,145,96), generated with Python stdlib zlib, no remote asset. */
export function mediaPng(): Buffer {
  return Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAGAAAABACAIAAABqVuVZAAAAeklEQVR4nO3QMQ0AIADAMHSgDks4xgEPBzuaTMDSMffSpfH9IB4gQIAAAQoHCBAgQIDCAQIECBCgcIAAAQIEKBwgQIAAAQoHCBAgQIDCAQIECBCgcIAAAQIEKBwgQIAAAQoHCBAgQIDCAQIECBCgcIAAAQIEKBwgQG8dtsEJeLWRyDYAAAAASUVORK5CYII=', 'base64');
}

/** One second of mono 8kHz PCM16: deterministic 400Hz square tone, amplitude 3000. */
export function mediaWav(): Buffer {
  const wav = Buffer.alloc(44 + 8_000 * 2);
  wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(8_000, 24); wav.writeUInt32LE(16_000, 28);
  wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(16_000, 40);
  for (let i = 0; i < 8_000; i++) wav.writeInt16LE(i % 20 < 10 ? 3000 : -3000, 44 + i * 2);
  return wav;
}

/** Fail closed before APIRequestContext or the SDK can contact storage. No signed URLs in evidence. */
export function localMediaUrl(value: string): URL {
  let url: URL;
  try { url = new URL(value); } catch { throw new Error('Media fixture requires a local HTTP URL'); }
  if (!['http:', 'https:'].includes(url.protocol) ||
      !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) ||
      url.username || url.password || url.search || url.hash) {
    throw new Error('Media fixture requires an unsigned loopback HTTP URL');
  }
  return url;
}

/** Auth headers stay in requests, never in returned fixture metadata. */
export function mediaActorHeaders(actor: TestActor): Record<string, string> {
  return { Authorization: `Bearer ${actor.tokens.access}`,
    'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId };
}

/** No redirects: a loopback URL must not turn into a remote media fetch. */
export async function readLocalMedia(request: APIRequestContext, value: string) {
  const url = localMediaUrl(value);
  return request.get(url.href, { timeout: REQUEST_TIMEOUT, maxRedirects: 0, maxRetries: 0 });
}

/**
 * Local-only storage contract (never read general AWS/S3 or production credentials):
 * E2E_MINIO_URL: public loopback origin; default from e2e/stack/e2e.env.
 * E2E_MINIO_BUCKET: exact upload bucket; default from tfc/settings/settings.py local mode.
 * E2E_MINIO_ACCESS_KEY / E2E_MINIO_SECRET_KEY: synthetic fixture credentials; defaults match
 * docker-compose.distributed.yml. Attach runtimes override these explicitly, without logging them.
 * The public image upload owns bucket creation/policy. This helper writes only its minted WAV key.
 */
export async function provisionMedia(request: APIRequestContext, actor: TestActor, prefix: string): Promise<OwnedMedia> {
  assertMarker(prefix);
  localMediaUrl(E2E.apiUrl);
  const origin = localMediaUrl(process.env.E2E_MINIO_URL ?? 'http://localhost:29005');
  if (origin.pathname !== '/') throw new Error('E2E_MINIO_URL must be an origin');
  const bucket = process.env.E2E_MINIO_BUCKET ?? 'fi-content-dev';
  if (!/^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(bucket)) throw new Error('Invalid local fixture bucket');
  const png = mediaPng();
  const uploaded = await request.post(E2E.apiUrl + UPLOAD_IMAGE, {
    headers: mediaActorHeaders(actor), timeout: REQUEST_TIMEOUT, maxRedirects: 0, maxRetries: 0,
    multipart: { type: 'image', files: { name: `${prefix}.png`, mimeType: 'image/png', buffer: png } },
  });
  if (uploaded.status() !== 200) throw new Error(`Fixture image upload returned HTTP ${uploaded.status()}`);
  const body = await uploaded.json();
  if (body.status !== true || !Array.isArray(body.result) || body.result.length !== 1 ||
      typeof body.result[0].url !== 'string' || body.result[0].error) {
    // Do not copy a storage error body (which may carry internal diagnostics) into the report.
    throw new Error('Fixture image upload did not return exactly one successful file');
  }
  const imageUrl = localMediaUrl(body.result[0].url);
  if (imageUrl.origin !== origin.origin || !imageUrl.pathname.startsWith(`/${bucket}/`)) {
    throw new Error('Uploaded image does not belong to the configured local fixture bucket');
  }
  const imageRead = await readLocalMedia(request, imageUrl.href);
  if (imageRead.status() !== 200 || mediaDigest(await imageRead.body()) !== mediaDigest(png)) {
    throw new Error('Uploaded fixture PNG bytes differ from the source');
  }
  const wav = mediaWav();
  const key = `${prefix}/good.wav`;
  const agent = origin.protocol === 'https:' ? new HttpsAgent() : new HttpAgent();
  const client = new Client({ transportAgent: agent, retryOptions: { disableRetry: true },
    endPoint: origin.hostname.replace(/^\[|\]$/g, ''),
    port: Number(origin.port || (origin.protocol === 'https:' ? 443 : 80)), useSSL: origin.protocol === 'https:',
    accessKey: process.env.E2E_MINIO_ACCESS_KEY ?? 'futureagi',
    secretKey: process.env.E2E_MINIO_SECRET_KEY ?? 'futureagi',
    region: 'us-east-1',
  });
  const deadline = setTimeout(() => agent.destroy(), REQUEST_TIMEOUT);
  try {
    // No audio-upload API / PyAV, bucket creation, policy changes, SQL or global cleanup.
    await client.putObject(bucket, key, wav, wav.length, { 'Content-Type': 'audio/wav' });
  } catch {
    // The SDK's error object can include signing/request details. Never serialize it.
    throw new Error('Could not seed the local fixture WAV with the maintained MinIO client');
  } finally {
    clearTimeout(deadline);
    agent.destroy();
  }
  const audioUrl = `${origin.origin}/${bucket}/${key}`;
  const audioRead = await readLocalMedia(request, audioUrl);
  if (audioRead.status() !== 200 || mediaDigest(await audioRead.body()) !== mediaDigest(wav)) {
    throw new Error('Seeded fixture WAV bytes differ from the source');
  }
  return { image: { url: imageUrl.href, sha256: mediaDigest(png), bytes: png.length },
    audio: { url: audioUrl, sha256: mediaDigest(wav), bytes: wav.length } };
}

function assertMarker(marker: string) {
  if (!/^[a-zA-Z0-9-]+$/.test(marker)) throw new Error('Media fixture marker must be alphanumeric with hyphens');
}

/** Inputs only. The flow independently defines the expected persisted matrix. */
export function mediaImportRows(marker: string, media: OwnedMedia): string[][] {
  assertMarker(marker);
  localMediaUrl(media.image.url); localMediaUrl(media.audio.url);
  return [['text', 'image', 'audio'], [`${marker}-blank`, '', ''],
    [`${marker}-valid`, media.image.url, media.audio.url],
    [`${marker}-invalid`, 'not-an-image-url', 'not-an-audio-url'],
    ...['nan', 'NA', 'null'].map(value => [`${marker}-literal-${value}`, value, value])];
}

function csv(rows: string[][]): Buffer {
  return Buffer.from(rows.map(row => row.map(value => /[",\r\n]/.test(value)
    ? `"${value.replaceAll('"', '""')}"` : value).join(',')).join('\r\n') + '\r\n');
}
const xml = (value: string) => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&apos;');

/** Minimal standard OOXML: inline strings preserve nan/NA/null; absent cells represent true blanks. */
function workbook(rows: string[][]): Buffer {
  const sheet = rows.map((row, i) => `<row r="${i + 1}">${row.map((value, j) => value === '' ? ''
    : `<c r="${String.fromCharCode(65 + j)}${i + 1}" t="inlineStr"><is><t>${xml(value)}</t></is></c>`).join('')}</row>`).join('');
  const files = {
    '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
    '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
    'xl/workbook.xml': '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Media" sheetId="1" r:id="rId1"/></sheets></workbook>',
    'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
    'xl/worksheets/sheet1.xml': `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:C${rows.length}"/><sheetData>${sheet}</sheetData></worksheet>`,
  };
  // Fixed ZIP timestamps make the fixture byte-reproducible as well as semantically deterministic.
  return Buffer.from(zipSync(Object.fromEntries(Object.entries(files).map(([name, value]) =>
    [name, [strToU8(value), { mtime: new Date('2000-01-01T00:00:00Z') }]])), { level: 6 }));
}

export function mediaImportFile(format: ImportFormat, marker: string, media: OwnedMedia) {
  const rows = mediaImportRows(marker, media);
  return { name: `${marker}.${format}`, mimeType: format === 'csv' ? 'text/csv'
    : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  buffer: format === 'csv' ? csv(rows) : workbook(rows) };
}

export function mediaSeedFile(marker: string, media: OwnedMedia) {
  assertMarker(marker);
  localMediaUrl(media.image.url); localMediaUrl(media.audio.url);
  return { name: `${marker}-seed.csv`, mimeType: 'text/csv',
    buffer: csv([['text', 'image', 'audio'], [`${marker}-seed`, media.image.url, media.audio.url]]) };
}

/** Browser CSV chooser upload; XLSX uses mediaImportFile's multipart payload, not this chooser. */
export async function uploadMediaCsv(input: Locator, marker: string, media: OwnedMedia) {
  await input.setInputFiles(mediaImportFile('csv', marker, media), { timeout: REQUEST_TIMEOUT });
}

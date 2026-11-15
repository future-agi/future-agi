import { test as offline, expect } from '@playwright/test';
import { strFromU8, unzipSync } from 'fflate';
import { test } from '../lib/fixtures';
import { localMediaUrl, mediaDigest, mediaImportFile, mediaPng, mediaSeedFile, mediaWav,
  provisionMedia, readLocalMedia, type OwnedMedia } from '../lib/media-import-fixture';

// Independently pinned from the fixed PNG and PCM format, not from downloaded objects.
const PNG_SHA256 = '768e4a90c25dcec3ae81a8fd1a176721350a5ab5c58014f4443a1321c39afd25';
const WAV_SHA256 = '7a4c40de8beede0ec3f528a0222b1edac3430b10f257a58bd17c625357d947a8';
const media: OwnedMedia = {
  image: { url: 'http://localhost:29005/fi-content-dev/selftest/good.png', sha256: PNG_SHA256, bytes: 179 },
  audio: { url: 'http://localhost:29005/fi-content-dev/selftest/good.wav', sha256: WAV_SHA256, bytes: 16_044 },
};

offline('media fixture: deterministic bytes, six literal-string rows and genuinely absent XLSX blanks', () => {
  expect(mediaDigest(mediaPng())).toBe(PNG_SHA256);
  expect(mediaPng().readUInt32BE(16)).toBe(96);
  expect(mediaPng().readUInt32BE(20)).toBe(64);
  expect(mediaDigest(mediaWav())).toBe(WAV_SHA256);
  expect(mediaWav().readUInt32LE(24)).toBe(8_000);
  expect(mediaWav().readUInt32LE(40) / mediaWav().readUInt32LE(28)).toBe(1);
  const expected = [['text', 'image', 'audio'], ['fixture-blank', '', ''],
    ['fixture-valid', media.image.url, media.audio.url],
    ['fixture-invalid', 'not-an-image-url', 'not-an-audio-url'],
    ['fixture-literal-nan', 'nan', 'nan'], ['fixture-literal-NA', 'NA', 'NA'],
    ['fixture-literal-null', 'null', 'null']];
  const csv = mediaImportFile('csv', 'fixture', media);
  expect(csv.buffer.toString()).toBe(expected.map(row => row.join(',')).join('\r\n') + '\r\n');
  expect(mediaSeedFile('fixture', media).buffer.toString()).toBe(
    `text,image,audio\r\nfixture-seed,${media.image.url},${media.audio.url}\r\n`);
  const xlsx = mediaImportFile('xlsx', 'fixture', media);
  expect(xlsx.buffer).toEqual(mediaImportFile('xlsx', 'fixture', media).buffer);
  const files = unzipSync(xlsx.buffer);
  expect(Object.keys(files).sort()).toEqual(['[Content_Types].xml', '_rels/.rels',
    'xl/_rels/workbook.xml.rels', 'xl/workbook.xml', 'xl/worksheets/sheet1.xml'].sort());
  const sheet = strFromU8(files['xl/worksheets/sheet1.xml']);
  const rows = [...sheet.matchAll(/<row r="(\d+)">(.*?)<\/row>/g)].map(match => ({
    row: Number(match[1]), cells: [...match[2].matchAll(/<c r="([A-C]\d+)" t="inlineStr"><is><t>(.*?)<\/t><\/is><\/c>/g)]
      .map(cell => ({ ref: cell[1], text: cell[2] })),
  }));
  expect(rows).toEqual(expected.map((row, i) => ({ row: i + 1,
    cells: row.flatMap((text, j) => text === '' ? [] : [{ ref: `${'ABC'[j]}${i + 1}`, text }]),
  })));
  expect(sheet).not.toMatch(/r="[BC]2"|<f[ >]/);
});

offline('media fixture: unsigned loopback only, including before HTTP reads', async ({ request }) => {
  for (const value of ['https://example.com/object.wav', 'http://localhost.evil.test/x',
    'file:///tmp/x', 'http://user:password@localhost/x', 'http://localhost/x?X-Amz-Signature=secret',
    'http://localhost/x#fragment', 'not-a-url']) {
    expect(() => localMediaUrl(value)).toThrow(/local|loopback/);
    await expect(readLocalMedia(request, value)).rejects.toThrow(/local|loopback/);
  }
  expect(localMediaUrl('http://localhost:29005/bucket/object.wav').pathname).toBe('/bucket/object.wav');
  expect(localMediaUrl('http://[::1]:29005/bucket/object.wav').hostname).toBe('[::1]');
});

test('media fixture: public PNG bootstrap and maintained MinIO WAV seeding preserve real local bytes',
  async ({ context, actor }, testInfo) => {
    test.setTimeout(120_000);
    const assets = await test.step('provision only owned local media', () =>
      provisionMedia(context.request, actor, `e2e-media-helper-${testInfo.workerIndex}-${Date.now().toString(36)}`),
    { timeout: 60_000 });
    await testInfo.attach('local-media-fixtures', { body: JSON.stringify(assets), contentType: 'application/json' });
    for (const [kind, expected] of [['image', PNG_SHA256], ['audio', WAV_SHA256]] as const) {
      const response = await readLocalMedia(context.request, assets[kind].url);
      expect(response.status()).toBe(200);
      expect(mediaDigest(await response.body())).toBe(expected);
      expect(assets[kind].sha256).toBe(expected);
      expect((await response.body()).length).toBe(assets[kind].bytes);
    }
  });

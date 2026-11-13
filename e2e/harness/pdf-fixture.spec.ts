import { test, expect } from '@playwright/test';
import { pdfFixture, uploadPdfFixture } from '../lib/pdf-fixture';

for (const unreadable of [false, true]) {
  test(`PDF fixture uploads exact synthetic bytes (unreadable=${unreadable})`, async ({ page }) => {
    const marker = 'e2e-pdf-harness';
    await page.setContent('<label>Document<input type="file" accept="application/pdf"></label>');
    const input = page.getByLabel('Document');
    const bytes = await uploadPdfFixture(input, marker, unreadable);
    expect(bytes).toEqual(pdfFixture(marker, unreadable));
    const uploaded = await input.evaluate(async (element: HTMLInputElement) => {
      const file = element.files![0];
      return { name: file.name, type: file.type, bytes: Array.from(new Uint8Array(await file.arrayBuffer())) };
    });
    expect(uploaded).toEqual({ name: `${marker}.pdf`, type: 'application/pdf', bytes: [...bytes] });
    if (!unreadable) {
      // latin1 maps each byte to one character, so string offsets are byte offsets.
      const source = bytes.toString('latin1');
      const xref = Number(source.match(/startxref\n(\d+)/)![1]);
      expect(source.slice(xref, xref + 4)).toBe('xref');
      const offsets = [...source.slice(xref).matchAll(/^(\d{10}) 00000 n $/gm)].map(match => Number(match[1]));
      expect(offsets).toHaveLength(8);
      offsets.forEach((offset, index) => expect(source.slice(offset)).toMatch(new RegExp(`^${index + 1} 0 obj\n`)));
      expect(source.match(/\/Type \/Page /g)).toHaveLength(2);
      const image = source.match(/\/Filter \/JPXDecode \/Length (\d+) >>\nstream\n/)!;
      const stream = bytes.subarray(image.index! + image[0].length, image.index! + image[0].length + Number(image[1]));
      // JP2 signature box, then the end-of-codestream marker.
      expect(stream.subarray(0, 12).toString('hex')).toBe('0000000c6a5020200d0a870a');
      expect(stream.subarray(-2).toString('hex')).toBe('ffd9');
    }
  });
}

test('PDF fixture rejects unescaped marker syntax', () => {
  expect(() => pdfFixture('invalid(marker)')).toThrow('PDF marker must be alphanumeric');
});

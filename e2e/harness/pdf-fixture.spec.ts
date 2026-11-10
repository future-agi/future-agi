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
      const source = bytes.toString();
      const xref = Number(source.match(/startxref\n(\d+)/)![1]);
      expect(source.slice(xref, xref + 4)).toBe('xref');
      expect(source.match(/\/Type \/Page /g)).toHaveLength(2);
    }
  });
}

test('PDF fixture rejects unescaped marker syntax', () => {
  expect(() => pdfFixture('invalid(marker)')).toThrow('PDF marker must be alphanumeric');
});

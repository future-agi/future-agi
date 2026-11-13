import { test, expect } from '@playwright/test';
import { PDF_EXTERNAL_LINK, pdfFixture, pdfNotes, uploadPdfFixture } from '../lib/pdf-fixture';

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
      expect(offsets).toHaveLength(12);
      offsets.forEach((offset, index) => expect(source.slice(offset)).toMatch(new RegExp(`^${index + 1} 0 obj\n`)));
      expect(source.match(/\/Type \/Page /g)).toHaveLength(2);
      // Both pages extend below y 0 so their notes can scroll to the top of the view.
      expect(source.match(/\/MediaBox \[0 -792 612 792\]/g)).toHaveLength(2);
      // Page 1 lists an internal link to page 2 (object 6), one URI link and two
      // links to a position: on page 1 itself (object 3) and on page 2.
      expect(source).toContain('/Annots [9 0 R 10 0 R 11 0 R 12 0 R]');
      expect(source).toMatch(/9 0 obj\n<< \/Type \/Annot \/Subtype \/Link [^\n]*\/Dest \[6 0 R \/Fit\] >>/);
      expect(source).toContain(`10 0 obj\n<< /Type /Annot /Subtype /Link /Rect [72 625 300 650] /Border [0 0 0] /A << /S /URI /URI (${PDF_EXTERNAL_LINK}) >> >>`);
      expect(source).toMatch(/11 0 obj\n<< \/Type \/Annot \/Subtype \/Link [^\n]*\/Dest \[3 0 R \/XYZ 0 100 null\] >>/);
      expect(source).toMatch(/12 0 obj\n<< \/Type \/Annot \/Subtype \/Link [^\n]*\/Dest \[6 0 R \/XYZ 0 -80 null\] >>/);
      // Each note sits just under its link's /XYZ top.
      expect(source).toContain(`72 80 Td (${pdfNotes(1, marker)}) Tj`);
      expect(source).toContain(`72 -100 Td (${pdfNotes(2, marker)}) Tj`);
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

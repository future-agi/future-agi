import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
interface Source {
  path: string;
  sha256: string;
  literal?: string;
  literalCount?: number;
}
interface Row {
  rowId: string;
  provenance: Source[];
}
const data = JSON.parse(readFileSync(new URL('../walks/contract-rows-w01-w02.json', import.meta.url), 'utf8')) as {
  rows: Row[];
};
const root = new URL('../../', import.meta.url);
const pins = new Map<string, Source>();
for (const row of data.rows)
  for (const source of row.provenance) pins.set(JSON.stringify([source.path, source.literal ?? null]), source);
for (const [index, source] of [...pins.values()].entries()) {
  test(`offline: W01/W02 product source pin ${index + 1} ${source.path}`, () => {
    // Manifest is data, never executable code or an import path. Fail closed on
    // forbidden sources or traversal before reading the assembled tree.
    expect(source.path.split('/').includes('..')).toBe(false);
    expect(
      /^(e2e\/|frontend\/src\/|futureagi\/(agentcc|tfc)\/|agentcc-gateway\/internal\/|bin\/e2e$)/.test(source.path),
    ).toBe(true);
    expect(/(^|\/)\.env|futureagi\/ee\/|ee-launch-config/.test(source.path)).toBe(false);
    const bytes = readFileSync(new URL(source.path, root));
    if (source.literal !== undefined)
      expect(bytes.toString('utf8').split(source.literal).length - 1).toBe(source.literalCount);
    else expect(createHash('sha256').update(bytes).digest('hex')).toBe(source.sha256);
  });
}

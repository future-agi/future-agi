// 708 add.78: controls of the capture-time hook (lib/trace-hygiene/), offline:
//   - the hook installs on this Playwright, and the values a test mints reach the
//     auditor's registry (lib/minted-values.ts, memory only);
//   - the kept trace carries trace-hygiene.json (scrubbed at capture time) and has no
//     finding even against those exact values;
//   - NEGATIVE CONTROL on that real trace: a value planted where the scrubber has no
//     rule (an unlisted header, an unlisted body field) survives the scrubber and the
//     independent auditor refuses it, with a value-free report.
import { randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { test, expect } from '../lib/scope-actors';
import { mint, mintedValues } from '../lib/minted-values';
import { MARKER, scrubTraceSet } from '../lib/trace-hygiene/archive.mjs';
import { readZip, writeZip } from '../lib/trace-hygiene/zip.mjs';
import { auditBuffer, auditPaths } from '../scripts/evidence-audit/audit.mjs';
import { findings, loopbackCredentialCalls } from './trace-hygiene-flow';

test.use({ trace: 'on' });
test.describe.configure({ mode: 'serial' });
let tracePath = '';
let minted: string[] = [];

test('offline: the capture-time hook is installed and the minted values are registered',
  async ({ playwright, _traceHygiene }, testInfo) => {
    expect(_traceHygiene, 'the capture-time hook installs on this Playwright').toBe(true);
    tracePath = testInfo.outputPath('trace.zip');
    const api = await playwright.request.newContext();
    try {
      await loopbackCredentialCalls(api, (...values) => {
        mint(...values);
        minted.push(...values);
      });
    } finally {
      await api.dispose();
    }
    expect(minted.length).toBe(6);
    for (const v of minted) expect(mintedValues().includes(v)).toBe(true);
  });

test('offline: the kept trace was scrubbed at capture time and is clean against the minted values', async () => {
  const report = auditPaths([tracePath], { root: path.dirname(tracePath), registry: mintedValues() });
  expect(report.scanned.archiveEntries).toBeGreaterThan(2);
  expect(report.problems).toEqual([]);
  expect(findings(report)).toEqual([]);
  expect(readZip(readFileSync(tracePath)).map((e) => e.name)).toContain(MARKER);
});

test('offline: the auditor refuses a value planted where the scrubber has no rule', async () => {
  const entries = readZip(readFileSync(tracePath)).filter((e) => e.name !== MARKER);
  const network = entries.find((e) => /trace\.network$/.test(e.name));
  expect(network, 'the trace has a network file').toBeTruthy();
  const grant = randomBytes(20).toString('hex');
  const sha1 = `${randomBytes(20).toString('hex')}.json`;
  let handoff = '';
  while (!/\d/.test(handoff) || !/[a-z]/.test(handoff) || !/[A-Z]/.test(handoff) || (handoff.match(/[-_]/g) ?? []).length > 3)
    handoff = randomBytes(32).toString('base64url');
  network!.data = Buffer.concat([network!.data, Buffer.from('\n' + JSON.stringify({ type: 'resource-snapshot', snapshot: {
    request: { url: 'http://127.0.0.1:9/model-hub/handoff/', headers: [{ name: 'X-Unlisted-Grant', value: grant }] },
    response: { content: { mimeType: 'application/json', _sha1: sha1 } } } }) + '\n')]);
  entries.push({ name: `resources/${sha1}`, data: Buffer.from(JSON.stringify({ handoff })) });
  const { buffers: [rescrubbed] } = scrubTraceSet([writeZip(entries)]);
  const text = readZip(rescrubbed).map((e) => e.data.toString('latin1')).join('\n');
  expect(text.includes(grant) && text.includes(handoff), 'control premise: the scrubber has no rule for these').toBe(true);
  const report = auditBuffer(rescrubbed, 'trace.zip');
  expect(report.refused).toBe(true);
  expect(report.hits.filter((h) => h.name === 'X-Unlisted-Grant' || h.name === 'handoff').map((h) => `${h.kind} ${h.name}`)
    .sort()).toEqual(['entropy-b64 handoff', 'entropy-hex X-Unlisted-Grant']);
  const reportText = JSON.stringify(report);
  expect(reportText.includes(grant) || reportText.includes(handoff), 'value-free report').toBe(false);
});

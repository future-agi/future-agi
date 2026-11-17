// H1: what every walk run records about itself, written as walk-receipt.json in
// the test's output directory and attached to the report:
//   - realHead: the checkout's HEAD as git reads it on the machine that ran the
//     walk, and dirty: 0 or 1 (tracked changes; the untracked futureagi/ee/cloud
//     never counts and is never read);
//   - productHead: the product commit the walk claims (E2E_WALK_PRODUCT_SHA) and
//     whether HEAD's product is identical to it (`git diff --quiet <sha> HEAD --
//     . <not HARNESS_PATHS>`), so a harness-only branch can stand for its base;
//   - overlay (E2E_WALK_OVERLAY_FROM, E2E_WALK_OVERLAY_PATHS): a walk proved on a
//     product head with the walk branch's harness paths checked out over it
//     (GATEWAY-CLOUD-RECIPE "Walk proofs: overlay the harness onto the feature
//     head"). realHead is then the product commit itself, dirty counts tracked
//     changes outside the overlay paths only, and overlay records the walk branch
//     commit, the paths and whether the working tree holds exactly that commit's
//     copy of them;
//   - the gateway profile and the mounted config file's digest (the managed-mock
//     inspector's receipt), written `sha256:<hex>` under a neutral field name;
//   - the boundary ledger (lib/walk/boundary.ts): every aborted non-local origin,
//     ASSETS-PENDING or FAIL;
//   - empty icons per page, attributed: ASSETS-PENDING where an icon host was
//     aborted in that context, PRODUCT-BUG where none was (network allowed);
//   - screenshots, which scripts/walk-contact-sheets.mjs turns into sheets;
//   - the browser settings (H11's exact pref list) and the walk's own verdicts.
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { Page, TestInfo } from '@playwright/test';
import { valueFreeFor } from '../value-free';
import type { BoundaryReceipt } from './boundary';
import { assertValueFreeReceipt } from './receipt-values.mjs';

const root = fileURLToPath(new URL('../../../', import.meta.url));

export const digest = (hex: string) => `sha256:${hex}`;

/** The harness-owned paths: launcher and test-mock code only, never product
 * (GATEWAY-CLOUD-RECIPE "Walk proofs"). Product identity ignores them, and an
 * overlay may carry only these. */
export const HARNESS_PATHS = ['e2e', 'bin/e2e', 'bin/lib', 'agentcc-gateway/cmd/agentcc-mcpmock',
  'agentcc-gateway/cmd/agentcc-otlpmock'] as const;
export interface OverlayState { from: string; paths: string[]; identical: boolean }
export interface SourceState {
  realHead: string | null; dirty: 0 | 1 | null; productHead: string | null; productIdentical: boolean | null;
  overlay?: OverlayState; note?: string;
}

const git = (args: string[]) => execFileSync('git', ['-C', root, ...args], { encoding: 'utf8', timeout: 30_000,
  stdio: ['ignore', 'pipe', 'pipe'] }).trim();
/** `git diff --quiet ...`: true when identical, false when different, throws otherwise. */
const same = (args: string[]): boolean => {
  try {
    execFileSync('git', ['-C', root, 'diff', '--quiet', ...args], { timeout: 60_000, stdio: 'ignore' });
    return true;
  } catch (error) {
    if ((error as { status?: number }).status !== 1) throw error;
    return false;
  }
};
const excluding = (paths: readonly string[]) => ['.', ...paths.map((p) => `:!${p}`)];

/** The overlay the walk declares, refused unless it is a commit and harness paths only. */
export function declaredOverlay(from = process.env.E2E_WALK_OVERLAY_FROM,
  paths = process.env.E2E_WALK_OVERLAY_PATHS): { from: string; paths: string[] } | null {
  if (!from) return null;
  if (!/^[0-9a-f]{40}$/.test(from)) throw new Error('E2E_WALK_OVERLAY_FROM is not a full commit id');
  const list = (paths ?? '').split(/\s+/).filter(Boolean).map((p) => p.replace(/\/+$/, ''));
  if (!list.length || list.some((p) => !(HARNESS_PATHS as readonly string[]).includes(p))) {
    throw new Error('E2E_WALK_OVERLAY_PATHS must name harness paths only');
  }
  return { from, paths: list };
}

/** HEAD, the tracked-tree state and the product identity, as git sees them here. */
export function sourceState(productSha = process.env.E2E_WALK_PRODUCT_SHA): SourceState {
  try {
    const overlay = declaredOverlay();
    const realHead = git(['rev-parse', 'HEAD']);
    const dirty = git(['status', '--porcelain', '--untracked-files=no', '--',
      ...(overlay ? excluding(overlay.paths) : [])]) === '' ? 0 : 1;
    const overlayState = overlay ? { overlay: { ...overlay, identical: same([overlay.from, '--', ...overlay.paths]) } } : {};
    if (!productSha) return { realHead, dirty, productHead: null, productIdentical: null, ...overlayState };
    if (!/^[0-9a-f]{40}$/.test(productSha)) throw new Error('E2E_WALK_PRODUCT_SHA is not a full commit id');
    const productIdentical = same([productSha, 'HEAD', '--', ...excluding(HARNESS_PATHS)]);
    return { realHead, dirty, productHead: productSha, productIdentical, ...overlayState };
  } catch (error) {
    return { realHead: null, dirty: null, productHead: productSha ?? null, productIdentical: null,
      note: `git unavailable: ${(error as Error).message.slice(0, 160)}` };
  }
}

/** OFF/ON/ABSENT/RECORDED are the census's (ON fails it); PASS and FAIL are a
 * walk's own backend assertions (lib/walk/money.ts checked()). */
export type Verdict = 'OFF' | 'ON' | 'ABSENT' | 'RECORDED' | 'PASS' | 'FAIL';
export interface VerdictRow { feature: string; lane: 'ui' | 'api' | 'storage' | 'gateway' | 'redis'; where: string;
  verdict: Verdict; detail?: unknown }
export interface EmptyIcons { context: string; page: string; count: number; attribution: 'ASSETS-PENDING' | 'PRODUCT-BUG' }

export class WalkReceipt {
  readonly verdicts: VerdictRow[] = [];
  readonly screenshots: string[] = [];
  readonly emptyIcons: EmptyIcons[] = [];
  readonly notes: string[] = [];
  extra: Record<string, unknown> = {};

  constructor(readonly walk: string, readonly testInfo: TestInfo) {}

  verdict(row: VerdictRow): VerdictRow { this.verdicts.push(row); return row; }

  /** A full-page screenshot under shots/<project>/, recorded for the contact sheets. */
  async shot(page: Page, name: string): Promise<void> {
    // HR-JRN-01: pixels cannot be hashed, so a value-free test records the shot as withheld.
    if (valueFreeFor(this.testInfo)) { this.notes.push(`screenshot withheld in value-free mode: ${name}`); return; }
    const safe = name.replace(/[^A-Za-z0-9_.-]+/g, '-').slice(0, 120);
    const file = this.testInfo.outputPath('shots', this.testInfo.project.name || 'default', `${safe}.png`);
    await page.screenshot({ path: file, fullPage: true });
    this.screenshots.push(file);
  }

  /** @iconify/react 4.1.1 drops every DOM prop on its unloaded class-less span.
   * Identify that span by its owning IconComponent, not every empty span on the
   * page (MUI also creates those). Its instance methods survive minification.
   * The real-package positive/negative control pins this React/Iconify seam. */
  async icons(page: Page, context: string, pageLabel: string, iconHostsAborted: boolean): Promise<void> {
    const count = await page.locator('span, svg.iconify, [class*="iconify"]').evaluateAll((els) =>
      els.filter((el) => {
        if (el.childElementCount || (el.textContent ?? '').trim()) return false;
        if (el.matches('svg.iconify, span.iconify, [class*="iconify"]')) return true;
        if (el.tagName !== 'SPAN' || el.attributes.length) return false;
        const key = Object.keys(el).find(k => k.startsWith('__reactFiber$'));
        const owner = key ? (el as any)[key]?.return?.stateNode : null;
        return owner?.state?.icon === null && typeof owner?.props?.icon === 'string' &&
          typeof owner._checkIcon === 'function' && typeof owner._abortLoading === 'function';
      }).length);
    if (count) this.emptyIcons.push({ context, page: pageLabel, count,
      attribution: iconHostsAborted ? 'ASSETS-PENDING' : 'PRODUCT-BUG' });
  }

  async write(fields: { profile: string; mountedConfig: string | null; boundary: BoundaryReceipt;
    browser: unknown }): Promise<Record<string, unknown>> {
    const body = {
      walk: this.walk, project: this.testInfo.project.name, title: this.testInfo.title,
      ...sourceState(), ...fields,
      images: process.env.E2E_WALK_IMAGES ? JSON.parse(readFileSync(process.env.E2E_WALK_IMAGES, 'utf8')) : null,
      // The verdict that matters: any ON fails the census; ABSENT is a feature this head lacks.
      verdicts: this.verdicts, on: this.verdicts.filter((v) => v.verdict === 'ON').length,
      failed: this.verdicts.filter((v) => v.verdict === 'FAIL').length,
      emptyIcons: this.emptyIcons, productBugIcons: this.emptyIcons.filter((i) => i.attribution === 'PRODUCT-BUG').length,
      screenshots: this.screenshots.map((f) => f.slice(this.testInfo.outputDir.length + 1)),
      notes: this.notes, ...this.extra, writtenAt: new Date().toISOString(),
    };
    // HR-JRN-01: a value-free test's receipt is serialized from its sanitized body (tokens, never values).
    const valueFree = valueFreeFor(this.testInfo);
    const json = JSON.stringify(valueFree ? valueFree.json(body) : body, null, 2);
    // Every receipt, in both modes, before any IO: the serialized text (the sanitized one in value-free mode) is
    // refused if it still carries an identity or a credential (receipt-values.mjs). Off mode keeps this guard as it
    // was; value-free mode adds the sanitizer in front of it, never in place of it.
    assertValueFreeReceipt(json);
    writeFileSync(this.testInfo.outputPath('walk-receipt.json'), json);
    await this.testInfo.attach('walk-receipt', { contentType: 'application/json', body: json });
    return body;
  }
}

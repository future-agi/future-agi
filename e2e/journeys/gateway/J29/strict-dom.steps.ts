import { expect, type Locator, type Page, type TestInfo } from '@playwright/test';
import type { Phase } from '../_shared/contract';
import { gatewaySelectors } from '../_shared/selectors';
import { assertBundledIcons, assertControlVisibility, assertLayout, assertLoadedInter,
  type ControlObservation, type InterObservation } from './strict-oracles';
import { partialStep } from './partial-step';

// First-paint budget follows e2e/README.md Writing a flow, not a lifecycle SLA.
// These functions consume an already authenticated, admitted page. They do NOT
// provision an actor, change a viewport, install an observer, navigate off-host,
// bind a missing Stage A fixture, or certify an expected/executed matrix.
export const UI_FIRST_PAINT_MS = 60_000;
const BUNDLED_FONT_READY = { timeout: UI_FIRST_PAINT_MS, intervals: [100, 250, 500] };
// Fixed bounded traversal, pinned to the existing navigation-focus.spec.ts ceiling.
export const FORWARD_TAB_LIMIT = 96;

export async function checkLayout(
  page: Page, info: TestInfo, admittedWidth: number, phase: Phase,
): Promise<void> {
  await partialStep(info, 'J29-GW-A02', 'horizontal-layout', phase, async () => {
    const observed = await page.evaluate(() => ({
      viewportWidth: window.innerWidth, documentWidth: document.documentElement.clientWidth,
      documentScrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
    }));
    assertLayout(observed, admittedWidth);
    await info.attach('j29-layout', { contentType: 'application/json',
      body: JSON.stringify({ admittedWidth, observed, completeRow: false }) });
  });
}

/** Read-only CSSOM View projection; no new selector, fixture, DOM write or fetch.
 * getBoundingClientRect is viewport-relative; clientLeft/Top exclude borders (and
 * the left scrollbar), clientWidth/Height exclude scrollbars. Axis-aligned scales
 * are converted to that same coordinate space; rotated/skewed/3D geometry refuses
 * rather than treating its bounding box as a measured rectangular scrollport.
 */
async function controlObservation(control: Locator): Promise<ControlObservation> {
  return control.evaluate(element => {
    const doc = element.ownerDocument;
    const view = doc.defaultView;
    if (!view || !element.isConnected) throw new Error('J29: detached control observation');
    const viewportWidth = doc.documentElement.clientWidth;
    const viewportHeight = view.innerHeight;
    const rect = element.getBoundingClientRect();
    const ancestors: ControlObservation['ancestors'] = [];
    const clips = (value: string) => ['hidden', 'auto', 'scroll', 'clip', 'overlay'].includes(value);
    const parent = (node: Element): Element | null => {
      if (node.assignedSlot) return node.assignedSlot;
      if (node.parentElement) return node.parentElement;
      const root = node.getRootNode();
      return root instanceof ShadowRoot ? root.host : null;
    };
    const rootStyle = view.getComputedStyle(doc.documentElement);
    for (let node: Element | null = element; node; node = parent(node)) {
      const style = view.getComputedStyle(node);
      const matrix = new DOMMatrixReadOnly(style.transform === 'none' ? undefined : style.transform);
      if (!matrix.is2D || matrix.b !== 0 || matrix.c !== 0 || matrix.a <= 0 || matrix.d <= 0 ||
          (style.rotate !== '' && style.rotate !== 'none') ||
          (style.scale !== '' && style.scale !== 'none')) {
        throw new Error('J29: unsupported control/ancestor transform geometry');
      }
      if (node === element) continue;
      // CSS Overflow propagates root overflow (and body overflow when the root
      // is visible in both axes) to the viewport, not the scrolled document box.
      const viewport = node === doc.documentElement || node === doc.scrollingElement ||
        (node === doc.body && rootStyle.overflowX === 'visible' && rootStyle.overflowY === 'visible');
      const paintContainment = /\b(paint|strict|content)\b/.test(style.contain);
      // Overflow does not create a scrollport on non-replaced inline boxes or
      // display:contents wrappers. The actual clipping ancestors still follow.
      const hasClipBox = style.display !== 'inline' && style.display !== 'contents';
      const clipsX = viewport || (hasClipBox && (clips(style.overflowX) || paintContainment));
      const clipsY = viewport || (hasClipBox && (clips(style.overflowY) || paintContainment));
      let left = 0, right = viewportWidth, top = 0, bottom = viewportHeight;
      if (!viewport && (clipsX || clipsY)) {
        if (!(node instanceof HTMLElement) || node.offsetWidth <= 0 || node.offsetHeight <= 0) {
          throw new Error('J29: clipping ancestor has no measurable HTML scrollport');
        }
        const box = node.getBoundingClientRect();
        const scaleX = box.width / node.offsetWidth;
        const scaleY = box.height / node.offsetHeight;
        left = box.left + node.clientLeft * scaleX;
        top = box.top + node.clientTop * scaleY;
        right = left + node.clientWidth * scaleX;
        bottom = top + node.clientHeight * scaleY;
      }
      ancestors.push({ left, right, top, bottom, clipsX, clipsY, scrollLeft: node.scrollLeft });
    }
    return { box: { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom },
      viewportWidth, viewportHeight, scrollX: view.scrollX, ancestors };
  });
}

/** Whole-control viewport AND rectangular ancestor-scrollport check, not merely
 * Playwright's some-visible-pixel predicate. Trial click is hit-testing only.
 * Vertical scrolling is allowed; initial horizontal clipping/scroll is rejected
 * BEFORE scrollIntoViewIfNeeded can hide it. Re-read after scrolling and after
 * trial click (which can itself scroll). Pixel/paint-shape proof stays owed A02.
 */
export async function checkUsableControl(control: Locator, info: TestInfo, phase: Phase): Promise<void> {
  await partialStep(info, 'J29-GW-A02', 'usable-control', phase, async () => {
    await expect(control).toHaveCount(1, { timeout: UI_FIRST_PAINT_MS });
    await expect(control).toBeVisible({ timeout: UI_FIRST_PAINT_MS });
    await expect(control).toBeEnabled({ timeout: UI_FIRST_PAINT_MS });
    assertControlVisibility(await controlObservation(control), 'before-scroll');
    await control.scrollIntoViewIfNeeded({ timeout: UI_FIRST_PAINT_MS });
    assertControlVisibility(await controlObservation(control), 'after-scroll');
    await control.click({ trial: true, timeout: UI_FIRST_PAINT_MS });
    assertControlVisibility(await controlObservation(control), 'after-scroll');
  });
}

/** The caller supplies SOURCE-PINNED start and target locators. No direct focus
 * is applied to the target; the only focus initialization is the start control.
 * Invoke at each required product state, including after a real reload. This is
 * not a general accessibility or reload-persistence certificate.
 */
export async function checkForwardTab(
  page: Page, start: Locator, target: Locator, info: TestInfo, phase: Phase,
): Promise<void> {
  await partialStep(info, 'J29-GW-A02', 'forward-tab', phase, async () => {
    await expect(start).toHaveCount(1, { timeout: UI_FIRST_PAINT_MS });
    await expect(target).toHaveCount(1, { timeout: UI_FIRST_PAINT_MS });
    await expect(start).toBeVisible({ timeout: UI_FIRST_PAINT_MS });
    await expect(target).toBeVisible({ timeout: UI_FIRST_PAINT_MS });
    await start.focus();
    expect(await target.evaluate(element => document.activeElement === element),
      'J29: the tab target must differ from the initial control').toBe(false);
    let reached = false;
    let tabs = 0;
    while (!reached && tabs < FORWARD_TAB_LIMIT) {
      await page.keyboard.press('Tab');
      tabs += 1;
      reached = await target.evaluate(element => document.activeElement === element);
    }
    await info.attach('j29-keyboard', { contentType: 'application/json',
      body: JSON.stringify({ tabs, limit: FORWARD_TAB_LIMIT, reached, completeRow: false }) });
    expect(reached, 'J29: source-pinned target must be forward-Tab reachable').toBe(true);
    await expect(target).toBeFocused();
  });
}

async function interObservation(text: Locator): Promise<InterObservation> {
  return text.evaluate(element => {
    const cleanFamily = (family: string) => family.trim().replace(/^['"]|['"]$/g, '');
    const latinFaces: InterObservation['latinFaces'] = [];
    // Do not call fonts.load() or add sample nodes: those can conceal a missing
    // product font use. The real, visible product heading must load the face.
    document.fonts.forEach(face => {
      const latin = face.unicodeRange.split(',').some(range => {
        const match = /^\s*U\+([0-9a-f]+)-([0-9a-f]+)\s*$/i.exec(range);
        return match !== null && parseInt(match[1], 16) === 0 && parseInt(match[2], 16) === 255;
      });
      if (cleanFamily(face.family) === 'Inter' && latin) {
        latinFaces.push({ family: cleanFamily(face.family), style: face.style,
          weight: face.weight, status: face.status });
      }
    });
    return { primaryFamily: cleanFamily(getComputedStyle(element).fontFamily.split(',')[0]), latinFaces };
  });
}

export async function checkLoadedInter(text: Locator, info: TestInfo, phase: Phase): Promise<void> {
  await partialStep(info, 'J29-GW-A08', 'loaded-latin-inter', phase, async () => {
    await expect(text).toHaveCount(1, { timeout: UI_FIRST_PAINT_MS });
    await expect(text).toBeVisible({ timeout: UI_FIRST_PAINT_MS });
    expect(await text.evaluate(element => /[A-Za-z]/.test(element.textContent ?? '')),
      'J29: font witness must be real nonempty Latin product text').toBe(true);
    await expect.poll(async () => {
      const observed = await interObservation(text);
      return observed.latinFaces.map(face => face.status);
    }, BUNDLED_FONT_READY).toEqual(['loaded']);
    assertLoadedInter(await interObservation(text));
  });
}

/** Only inspect masks. No fetch, route fulfillment, Image load or nonlocal
 * injection is added. Network coverage remains with the accepted base observer.
 * `root` and the complete expected icon multiset must be pinned by the consumer
 * to the offered product state (desktop/mobile rails have different inventory).
 */
export async function checkBundledIconStructure(
  root: Locator, expectedNames: readonly string[], info: TestInfo, phase: Phase,
): Promise<void> {
  await partialStep(info, 'J29-GW-A08', 'bundled-icon-structure', phase, async () => {
    await expect(root).toHaveCount(1, { timeout: UI_FIRST_PAINT_MS });
    await expect(root).toBeVisible({ timeout: UI_FIRST_PAINT_MS });
    const icons = gatewaySelectors.icons(root);
    await expect(icons).toHaveCount(expectedNames.length, { timeout: UI_FIRST_PAINT_MS });
    const observed = await icons.evaluateAll(elements => elements.map(element => {
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      // Pinned to gatewayIconSrc's data:image/svg+xml;base64 encoding. A CSS
      // URL from another origin is rejected structurally, never requested here.
      const mask = /^url\(["']?(data:image\/svg\+xml;base64,[A-Za-z0-9+/=]+)["']?\)$/.exec(style.maskImage);
      let nonemptyGlyph = false;
      if (mask) {
        const xml = atob(mask[1].slice('data:image/svg+xml;base64,'.length));
        const svg = new DOMParser().parseFromString(xml, 'image/svg+xml');
        const safe = svg.documentElement.localName === 'svg' &&
          svg.querySelectorAll('parsererror, image, use, script, foreignObject, style').length === 0;
        nonemptyGlyph = safe && Array.from(svg.querySelectorAll('path, circle, rect, polyline')).some(node => {
          if (node.localName === 'path') return Boolean(node.getAttribute('d')?.trim());
          if (node.localName === 'circle') return Number(node.getAttribute('r')) > 0;
          if (node.localName === 'rect') return Number(node.getAttribute('width')) > 0 && Number(node.getAttribute('height')) > 0;
          return Boolean(node.getAttribute('points')?.trim());
        });
      }
      let painted = true;
      for (let node: Element | null = element; node !== null; node = node.parentElement) {
        const parentStyle = getComputedStyle(node);
        if (parentStyle.display === 'none' || parentStyle.visibility !== 'visible' || Number(parentStyle.opacity) <= 0) painted = false;
      }
      const transparent = style.backgroundColor === 'transparent' || /rgba\([^)]*,\s*0\s*\)/.test(style.backgroundColor);
      return { name: element.getAttribute('data-gateway-icon') ?? '', width: box.width, height: box.height,
        painted: painted && !transparent, bundledSvgMask: mask !== null, nonemptyGlyph };
    }));
    assertBundledIcons(observed, expectedNames);
  });
}

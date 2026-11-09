import { expect } from '@playwright/test';

// These are DOM/state projection assertions, NOT a second runner, matrix
// reconciler or acceptance layer. Their synthetic controls cannot qualify J29.
export interface LayoutObservation {
  viewportWidth: number;
  documentWidth: number;
  documentScrollWidth: number;
  bodyScrollWidth: number;
}

export function assertLayout(observed: LayoutObservation, admittedWidth: number): void {
  expect(Number.isSafeInteger(admittedWidth) && admittedWidth >= 320,
    'J29: width must be explicitly admitted and include the separately inventoried 320px cell').toBe(true);
  expect(Object.values(observed).every(value => Number.isFinite(value) && value > 0),
    'J29: missing layout observation is not zero overflow').toBe(true);
  expect(observed.viewportWidth, 'J29: the spec must not replace the runner viewport').toBe(admittedWidth);
  expect(observed.documentWidth).toBeLessThanOrEqual(observed.viewportWidth);
  expect(observed.documentScrollWidth, 'J29: document horizontal overflow').toBeLessThanOrEqual(observed.documentWidth);
  expect(observed.bodyScrollWidth, 'J29: body horizontal overflow').toBeLessThanOrEqual(observed.documentWidth);
}

export interface ControlRect {
  left: number; right: number; top: number; bottom: number;
}
export interface ControlObservation {
  box: ControlRect;
  viewportWidth: number;
  viewportHeight: number;
  scrollX: number;
  ancestors: (ControlRect & { clipsX: boolean; clipsY: boolean; scrollLeft: number })[];
}

/** CSSOM View rectangles/client scrollports, captured by strict-dom.steps.ts.
 * Always check the horizontal geometry BEFORE any scroll, then again after each
 * scroll-capable action. Only vertical clipping may be resolved by scrolling.
 * This is rectangular scrollport coverage, not a pixel/paint-shape certificate.
 */
export function assertControlVisibility(
  observed: ControlObservation, stage: 'before-scroll' | 'after-scroll',
): void {
  const { box, viewportWidth, viewportHeight, scrollX, ancestors } = observed;
  expect([box.left, box.right, box.top, box.bottom, viewportWidth, viewportHeight, scrollX]
    .every(Number.isFinite), 'J29: finite control observation required').toBe(true);
  expect(viewportWidth).toBeGreaterThan(0);
  expect(viewportHeight).toBeGreaterThan(0);
  expect(box.right).toBeGreaterThan(box.left);
  expect(box.bottom).toBeGreaterThan(box.top);
  expect(box.left).toBeGreaterThanOrEqual(0);
  expect(box.right).toBeLessThanOrEqual(viewportWidth);
  expect(scrollX, 'J29: control must not require sideways scrolling').toBe(0);
  // A connected product control has at least its document root as an ancestor.
  expect(ancestors.length, 'J29: missing control ancestor observation').toBeGreaterThan(0);
  if (stage === 'after-scroll') {
    expect(box.top).toBeGreaterThanOrEqual(0);
    expect(box.bottom).toBeLessThanOrEqual(viewportHeight);
  }
  for (const ancestor of ancestors) {
    expect([ancestor.left, ancestor.right, ancestor.top, ancestor.bottom, ancestor.scrollLeft]
      .every(Number.isFinite), 'J29: finite ancestor observation required').toBe(true);
    expect(ancestor.scrollLeft, 'J29: ancestor must not require sideways scrolling').toBe(0);
    if (ancestor.clipsX) {
      expect(box.left, 'J29: control left edge clipped by ancestor').toBeGreaterThanOrEqual(ancestor.left);
      expect(box.right, 'J29: control right edge clipped by ancestor').toBeLessThanOrEqual(ancestor.right);
    }
    if (stage === 'after-scroll' && ancestor.clipsY) {
      expect(box.top, 'J29: control top edge clipped by ancestor').toBeGreaterThanOrEqual(ancestor.top);
      expect(box.bottom, 'J29: control bottom edge clipped by ancestor').toBeLessThanOrEqual(ancestor.bottom);
    }
  }
}

export interface InterObservation {
  primaryFamily: string;
  latinFaces: { family: string; style: string; weight: string; status: string }[];
}

// Provenance: frontend/src/main.jsx imports assets/fonts/fonts.css;
// fonts.css:68-78 declares exactly one normal Latin Inter face (100..900).
// theme/ds-tokens.js dsTypography.fontFamily puts Inter first. A fonts.check()
// alone passes for a nonexistent face; enumerate the actual declared face too.
export function assertLoadedInter(observed: InterObservation): void {
  expect(observed.primaryFamily, 'J29: visible product text must use Inter first').toBe('Inter');
  expect(observed.latinFaces, 'J29: missing, duplicate or failed Latin Inter face').toEqual([
    { family: 'Inter', style: 'normal', weight: '100 900', status: 'loaded' },
  ]);
}

export interface IconObservation {
  name: string;
  width: number;
  height: number;
  painted: boolean;
  bundledSvgMask: boolean;
  nonemptyGlyph: boolean;
}

// Provenance: gateway/shell/gateway-icons.jsx and components/svg-color/svg-color.jsx.
// SvgColor paints an inline data-SVG mask, not an <svg> child. Check the exact
// requested multiset, preserving duplicates. CSS/resource structure is only a
// partial witness; real pixel/render and all-context network controls stay owed.
export function assertBundledIcons(observed: readonly IconObservation[], expectedNames: readonly string[]): void {
  expect(expectedNames.length, 'J29: an empty icon inventory cannot pass').toBeGreaterThan(0);
  expect(observed.map(icon => icon.name).sort(), 'J29: exact icon multiset').toEqual([...expectedNames].sort());
  for (const icon of observed) {
    expect(Number.isFinite(icon.width) && icon.width > 0, 'J29: icon has width').toBe(true);
    expect(Number.isFinite(icon.height) && icon.height > 0, 'J29: icon has height').toBe(true);
    expect(icon.painted, 'J29: hidden or transparent icon').toBe(true);
    expect(icon.bundledSvgMask, 'J29: icon must use the bundled data-SVG mask').toBe(true);
    expect(icon.nonemptyGlyph, 'J29: empty SVG is not a usable icon').toBe(true);
  }
}

import { randomUUID } from 'node:crypto';
import { test } from '@playwright/test';
import { attachSeededIds, journeyTitle, type Phase } from '../_shared/contract';
import type { AssertionRow } from '../_shared/packet-rows';
import { partialStep } from './partial-step';
import { assertBundledIcons, assertControlVisibility, assertLayout, assertLoadedInter,
  type ControlObservation, type IconObservation, type InterObservation, type LayoutObservation } from './strict-oracles';

// DATA-ONLY DETECTOR TESTS, NOT PRODUCT WALKS. No page/request/actor fixture is
// requested. Do not import these as the row inventory of an offered surface.
// C_MATRIX's actual dropped-browser/width/layout scratch controls and
// C_NONLOCAL_AND_ASSETS's actual removed-asset/all-context controls remain owed.
// These tests carry row IDs under journey-oracle, never journey-assertion.
const layout = (): LayoutObservation => ({ viewportWidth: 320, documentWidth: 320,
  documentScrollWidth: 320, bodyScrollWidth: 320 });
// Synthetic projection of SOL-02's 200px control at x=80. Its center stays
// within even the red 160px scrollport and the document never overflows. Keep
// a non-clipping parent first so an implementation checking only that parent
// cannot pass the control. Restored phases recreate the original wide clip.
const controlGeometry = (): ControlObservation => ({
  box: { left: 80, right: 280, top: 100, bottom: 140 },
  viewportWidth: 320, viewportHeight: 640, scrollX: 0,
  ancestors: [
    { left: 0, right: 320, top: 0, bottom: 640, clipsX: false, clipsY: false, scrollLeft: 0 },
    { left: 80, right: 300, top: 80, bottom: 160, clipsX: true, clipsY: true, scrollLeft: 0 },
  ],
});
const font = (): InterObservation => ({ primaryFamily: 'Inter', latinFaces: [
  { family: 'Inter', style: 'normal', weight: '100 900', status: 'loaded' },
] });
const icon = (): IconObservation => ({ name: 'keys', width: 16, height: 16,
  painted: true, bundledSvgMask: true, nonemptyGlyph: true });

interface DetectorControl {
  id: string;
  row: AssertionRow<'J29'>;
  packetControl: 'C_MATRIX' | 'C_NONLOCAL_AND_ASSETS';
  expectedFailureMessage: string;
  check: (red: boolean) => void;
}
const controls: readonly DetectorControl[] = [
  { id: 'document-overflow', row: 'J29-GW-A02', packetControl: 'C_MATRIX',
    expectedFailureMessage: 'J29: document horizontal overflow',
    check: red => assertLayout({ ...layout(), documentScrollWidth: red ? 321 : 320 }, 320) },
  { id: 'body-overflow', row: 'J29-GW-A02', packetControl: 'C_MATRIX',
    expectedFailureMessage: 'J29: body horizontal overflow',
    check: red => assertLayout({ ...layout(), bodyScrollWidth: red ? 321 : 320 }, 320) },
  { id: 'wrong-viewport', row: 'J29-GW-A02', packetControl: 'C_MATRIX',
    expectedFailureMessage: 'J29: the spec must not replace the runner viewport',
    check: red => assertLayout({ ...layout(), viewportWidth: red ? 1440 : 320 }, 320) },
  { id: 'ancestor-horizontal-clip', row: 'J29-GW-A02', packetControl: 'C_MATRIX',
    expectedFailureMessage: 'J29: control right edge clipped by ancestor',
    check: red => {
      const observed = controlGeometry();
      if (red) observed.ancestors[1].right = 240;
      assertControlVisibility(observed, 'before-scroll');
    } },
  { id: 'ancestor-vertical-clip', row: 'J29-GW-A02', packetControl: 'C_MATRIX',
    expectedFailureMessage: 'J29: control bottom edge clipped by ancestor',
    check: red => {
      const observed = controlGeometry();
      if (red) observed.ancestors[1].bottom = 130;
      assertControlVisibility(observed, 'after-scroll');
    } },
  { id: 'ancestor-sideways-scroll-before', row: 'J29-GW-A02', packetControl: 'C_MATRIX',
    expectedFailureMessage: 'J29: ancestor must not require sideways scrolling',
    check: red => {
      const observed = controlGeometry();
      // Even if horizontal scrolling made all edges visible, it cannot qualify.
      if (red) observed.ancestors[1].scrollLeft = 20;
      assertControlVisibility(observed, 'before-scroll');
    } },
  { id: 'ancestor-sideways-scroll-after', row: 'J29-GW-A02', packetControl: 'C_MATRIX',
    expectedFailureMessage: 'J29: ancestor must not require sideways scrolling',
    check: red => {
      const observed = controlGeometry();
      if (red) observed.ancestors[1].scrollLeft = 20;
      assertControlVisibility(observed, 'after-scroll');
    } },
  { id: 'missing-font', row: 'J29-GW-A08', packetControl: 'C_NONLOCAL_AND_ASSETS',
    expectedFailureMessage: 'J29: missing, duplicate or failed Latin Inter face',
    check: red => assertLoadedInter(red ? { ...font(), latinFaces: [] } : font()) },
  { id: 'failed-font', row: 'J29-GW-A08', packetControl: 'C_NONLOCAL_AND_ASSETS',
    expectedFailureMessage: 'J29: missing, duplicate or failed Latin Inter face',
    check: red => assertLoadedInter({ ...font(), latinFaces: font().latinFaces.map(face =>
      ({ ...face, status: red ? 'error' : 'loaded' })) }) },
  { id: 'duplicate-font', row: 'J29-GW-A08', packetControl: 'C_NONLOCAL_AND_ASSETS',
    expectedFailureMessage: 'J29: missing, duplicate or failed Latin Inter face',
    check: red => assertLoadedInter(red ? { ...font(), latinFaces: [...font().latinFaces, ...font().latinFaces] } : font()) },
  { id: 'missing-icon', row: 'J29-GW-A08', packetControl: 'C_NONLOCAL_AND_ASSETS',
    expectedFailureMessage: 'J29: exact icon multiset',
    check: red => assertBundledIcons(red ? [] : [icon()], ['keys']) },
  { id: 'empty-glyph', row: 'J29-GW-A08', packetControl: 'C_NONLOCAL_AND_ASSETS',
    expectedFailureMessage: 'J29: empty SVG is not a usable icon',
    check: red => assertBundledIcons([{ ...icon(), nonemptyGlyph: !red }], ['keys']) },
  { id: 'hidden-icon', row: 'J29-GW-A08', packetControl: 'C_NONLOCAL_AND_ASSETS',
    expectedFailureMessage: 'J29: hidden or transparent icon',
    check: red => assertBundledIcons([{ ...icon(), painted: !red }], ['keys']) },
  { id: 'extra-icon', row: 'J29-GW-A08', packetControl: 'C_NONLOCAL_AND_ASSETS',
    expectedFailureMessage: 'J29: exact icon multiset',
    check: red => assertBundledIcons(red ? [icon(), icon()] : [icon()], ['keys']) },
];

for (const control of controls) {
  for (const phase of ['baseline', 'red', 'restored'] as const satisfies readonly Phase[]) {
    test(journeyTitle('J29', `oracle-${control.id}`, phase), {
      tag: ['@journey-J29', '@j29-oracle-only'],
      annotation: [{ type: 'journey-oracle', description: JSON.stringify({
        schema: 'j29-detector-control/v1', packet: 'J29', row: control.row, phase,
        control: control.packetControl, detectorCase: control.id, completeRow: false,
        disposition: 'NOT_WALKED', expectedFailureMessage: phase === 'red' ? control.expectedFailureMessage : '',
      }) }],
    }, async ({}, info) => {
      // Attach before expected-failure marking: setup/attachment failure is not
      // an adequate red. The runtime must still inspect the exact expect failure
      // and retain separate originals; an expected-fail result alone isn't proof.
      await attachSeededIds(info, 'J29', { runIds: [randomUUID()] });
      test.fail(phase === 'red', `Oracle-only ${control.packetControl}: ${control.expectedFailureMessage}`);
      await partialStep(info, control.row, control.id, phase, async () => control.check(phase === 'red'));
    });
  }
}

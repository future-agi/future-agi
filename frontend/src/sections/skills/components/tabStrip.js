import { useEffect } from 'react';

// Shared behaviour for Skills tab strips (primary navigation and object tabs).
// - The theme gives scroll buttons 48px; a disabled start button kept that
//   space and indented the first tab ~50px at 390px. Disabled buttons now
//   collapse, so the first tab starts on the route gutter.
// - Buttons stay available on phones (pass allowScrollButtonsMobile).
// - While the strip overflows, its edges fade over 24px, so a label cut at the
//   edge reads as "more this way" rather than as a broken word. The fade sits
//   on the side(s) that have more tabs and spans the cut label exactly;
//   useActiveTabInView sets the flags and widths.
// - Tabs snap to their start edge (UX iteration 3, SH1): whatever scrolls the
//   strip (the arrows, a swipe, the selection), the first visible tab starts
//   exactly at the strip's left edge, so a cut label can only appear on the
//   right, under the fade. Measured at 390 before this: the mask was applied,
//   but centring left 25-30px of a neighbour's label crisp on both sides.
const FADE = 24;
// Width of one scroll arrow (below). Disabled arrows take no space.
const BUTTON = 32;
export const scrollTabsSx = {
  minWidth: 0,
  '& .MuiTabs-scrollButtons': { width: BUTTON, flexShrink: 0 },
  '& .MuiTabs-scroller .MuiTab-root': { scrollSnapAlign: 'start' },
  '& .MuiTabs-scrollButtons.Mui-disabled': { display: 'none' },
  '& .MuiTabs-scroller[data-fade-start="true"], & .MuiTabs-scroller[data-fade-end="true"]': {
    WebkitMaskImage: 'var(--tab-fade)',
    maskImage: 'var(--tab-fade)',
  },
  // A mask uses only alpha: "black" is fully shown, "transparent" fully faded.
  '& .MuiTabs-scroller': {
    scrollSnapType: 'x mandatory',
    '--tab-fade-start': 'black',
    '--tab-fade-end': 'black',
    '--tab-fade': `linear-gradient(to right, var(--tab-fade-start), black ${FADE}px, black calc(100% - ${FADE}px), var(--tab-fade-end))`,
  },
  '& .MuiTabs-scroller[data-fade-start="true"]': { '--tab-fade-start': 'transparent' },
  '& .MuiTabs-scroller[data-fade-end="true"]': { '--tab-fade-end': 'transparent' },
};

// Props every Skills tab strip uses so later tabs are reachable on a phone.
export const scrollTabsProps = { variant: 'scrollable', scrollButtons: 'auto', allowScrollButtonsMobile: true };

/**
 * Marks which edges of an overflowing scroller have hidden tabs, and sizes
 * each fade to the label cut at that edge: the whole fragment sits under the
 * fade (never a crisp half word), and an edge that falls exactly on a tab
 * boundary gets no fade over the whole tab beside it (its arrow is the cue).
 * The stylesheet's FADE px gradient is only the fallback before this runs.
 */
export function markTabFade(scroller) {
  const overflow = scroller.scrollWidth - scroller.clientWidth > 1;
  const start = overflow && scroller.scrollLeft > 1;
  const end = overflow && scroller.scrollLeft + scroller.clientWidth < scroller.scrollWidth - 1;
  scroller.setAttribute('data-fade-start', String(start));
  scroller.setAttribute('data-fade-end', String(end));
  if (!start && !end) {
    scroller.style.removeProperty('--tab-fade');
    return;
  }
  const edge = scroller.getBoundingClientRect();
  const right = edge.left + scroller.clientWidth;
  let head = 0;
  let tail = 0;
  scroller.querySelectorAll('[role="tab"]').forEach((tab) => {
    const r = tab.getBoundingClientRect();
    if (r.left < edge.left - 0.5 && r.right > edge.left + 0.5) head = Math.max(head, r.right - edge.left);
    if (r.left < right - 0.5 && r.right > right + 0.5) tail = Math.max(tail, right - r.left);
  });
  // Fragment widths, rounded; the inner 40% of a fragment eases out, the rest is nearly gone.
  const stops = (width) => [Math.round(width), Math.round(width * 0.6)];
  const [hw, hm] = stops(head);
  const [tw, tm] = stops(tail);
  const from = !start ? 'black 0px' : hw > 0 ? `transparent 0px, rgba(0, 0, 0, 0.35) ${hm}px, black ${hw}px` : 'black 0px';
  const to = !end ? 'black 100%' : tw > 0 ? `black calc(100% - ${tw}px), rgba(0, 0, 0, 0.35) calc(100% - ${tm}px), transparent 100%` : 'black 100%';
  scroller.style.setProperty('--tab-fade', `linear-gradient(to right, ${from}, ${to})`);
}

/**
 * Brings the selected tab fully into view by the least scroll that lands on a
 * tab boundary: the first visible tab starts exactly at the scroller's left
 * edge. A tab that is already fully visible stays put unless a neighbour is
 * cut at the left, in which case the strip settles on the nearest boundary
 * that keeps the selection whole. Only the scroller moves: never
 * Element.scrollIntoView, which also scrolls every scrollable ancestor (the
 * dashboard frame and the page included). The export keeps its old name.
 */
export function centreActiveTab(root) {
  const scroller = root?.querySelector('.MuiTabs-scroller');
  if (!scroller) return;
  const tab = scroller.querySelector('[role="tab"][aria-selected="true"]');
  if (tab && scroller.scrollWidth - scroller.clientWidth > 1) {
    const target = boundaryTarget(scroller, tab);
    if (target != null && Math.abs(target - scroller.scrollLeft) > 0.5) {
      if (typeof scroller.scrollTo === 'function') scroller.scrollTo({ left: target });
      else scroller.scrollLeft = target;
    }
  }
  markTabFade(scroller);
}

/** The scroll offset (content px) centreActiveTab moves to, or null to stay. */
export function boundaryTarget(scroller, tab) {
  const current = scroller.scrollLeft;
  const origin = scroller.getBoundingClientRect().left - current;
  const box = (el) => { const r = el.getBoundingClientRect(); return { left: r.left - origin, right: r.right - origin }; };
  const tabs = [...scroller.querySelectorAll('[role="tab"]')].map(box);
  const active = box(tab);
  const total = scroller.scrollWidth;
  // The arrows show only while there is more strip that way, and each takes
  // BUTTON px from the scroller, so the visible width depends on the offset.
  const arrows = scroller.parentElement?.querySelectorAll(':scope > .MuiTabs-scrollButtons').length ? BUTTON : 0;
  const shown = scroller.clientWidth + (current > 1 ? arrows : 0) + (current + scroller.clientWidth < total - 1 ? arrows : 0);
  const widthAt = (offset) => {
    const start = offset > 1 ? arrows : 0;
    const end = offset + shown - start < total - 1 ? arrows : 0;
    return shown - start - end;
  };
  const fits = (offset) => offset <= active.left + 0.5 && offset + widthAt(offset) >= active.right - 0.5;
  const reachable = (offset) => offset <= total - widthAt(offset) + 0.5;
  const visible = current <= active.left + 0.5 && current + scroller.clientWidth >= active.right - 0.5;
  const aligned = tabs.some((t) => Math.abs(t.left - current) <= 0.5) || current < 0.5;
  if (visible && aligned) return null;
  const candidates = [0, ...tabs.map((t) => t.left)].filter((offset) => fits(offset) && reachable(offset));
  if (!candidates.length) return Math.max(0, Math.min(active.left, total - widthAt(active.left)));
  return candidates.reduce((best, offset) => (Math.abs(offset - current) < Math.abs(best - current) ? offset : best));
}

/**
 * Keeps the selected tab fully visible, aligned to a tab boundary, on mount,
 * whenever the selection changes, when the strip resizes and once the fonts
 * have loaded (labels change width when the app font arrives). `ref` points at
 * the Tabs root or any ancestor that contains exactly one tab strip.
 */
export function useActiveTabInView(ref, value) {
  useEffect(() => {
    const root = ref.current;
    if (!root || typeof window === 'undefined') return undefined;
    let frame = 0;
    let alive = true;
    const run = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => { if (alive) centreActiveTab(root); });
    };
    run();
    document.fonts?.ready?.then(run).catch(() => undefined);
    const scroller = root.querySelector('.MuiTabs-scroller');
    const fade = () => scroller && markTabFade(scroller);
    scroller?.addEventListener('scroll', fade, { passive: true });
    let observer;
    if (scroller && typeof ResizeObserver !== 'undefined') {
      // Re-align only when a width changes. Watch the whole strip (scroller
      // plus arrows), not the scroller: an arrow appearing after a scroll
      // narrows the scroller (and can change the strip's height), and
      // re-aligning then would pull the strip back to the selection, undoing
      // the arrow press (measured at 390: every press bounced back).
      const widths = new WeakMap();
      observer = new ResizeObserver((entries) => {
        const changed = entries.some((entry) => {
          const width = Math.round(entry.contentRect.width);
          const previous = widths.get(entry.target);
          widths.set(entry.target, width);
          return previous === undefined || previous !== width;
        });
        if (changed) run();
      });
      observer.observe(scroller.parentElement || scroller);
      // The tab row itself (its width changes when the app font arrives).
      const strip = scroller.querySelector('.MuiTabs-flexContainer') || scroller.firstElementChild;
      if (strip) observer.observe(strip);
    }
    return () => {
      alive = false;
      window.cancelAnimationFrame(frame);
      scroller?.removeEventListener('scroll', fade);
      observer?.disconnect();
    };
  }, [ref, value]);
}

import { useEffect } from 'react';

// Shared behaviour for Skills tab strips (primary navigation and object tabs).
// - The theme gives scroll buttons 48px; a disabled start button kept that
//   space and indented the first tab ~50px at 390px. Disabled buttons now
//   collapse, so the first tab starts on the route gutter.
// - Buttons stay available on phones (pass allowScrollButtonsMobile).
// - While the strip overflows, its edges fade over 24px, so a label cut at the
//   edge reads as "more this way" rather than as a broken word. The fade sits
//   on the side(s) that have more tabs; useActiveTabInView sets the flags.
const FADE = 24;
export const scrollTabsSx = {
  minWidth: 0,
  '& .MuiTabs-scrollButtons': { width: 32, flexShrink: 0 },
  '& .MuiTabs-scrollButtons.Mui-disabled': { display: 'none' },
  '& .MuiTabs-scroller[data-fade-start="true"], & .MuiTabs-scroller[data-fade-end="true"]': {
    WebkitMaskImage: 'var(--tab-fade)',
    maskImage: 'var(--tab-fade)',
  },
  // A mask uses only alpha: "black" is fully shown, "transparent" fully faded.
  '& .MuiTabs-scroller': {
    '--tab-fade-start': 'black',
    '--tab-fade-end': 'black',
    '--tab-fade': `linear-gradient(to right, var(--tab-fade-start), black ${FADE}px, black calc(100% - ${FADE}px), var(--tab-fade-end))`,
  },
  '& .MuiTabs-scroller[data-fade-start="true"]': { '--tab-fade-start': 'transparent' },
  '& .MuiTabs-scroller[data-fade-end="true"]': { '--tab-fade-end': 'transparent' },
};

// Props every Skills tab strip uses so later tabs are reachable on a phone.
export const scrollTabsProps = { variant: 'scrollable', scrollButtons: 'auto', allowScrollButtonsMobile: true };

/** Marks which edges of an overflowing scroller have hidden tabs. */
export function markTabFade(scroller) {
  const overflow = scroller.scrollWidth - scroller.clientWidth > 1;
  const start = overflow && scroller.scrollLeft > 1;
  const end = overflow && scroller.scrollLeft + scroller.clientWidth < scroller.scrollWidth - 1;
  scroller.setAttribute('data-fade-start', String(start));
  scroller.setAttribute('data-fade-end', String(end));
}

/**
 * Centres the selected tab inside its own scroller. Only the scroller moves:
 * never Element.scrollIntoView, which also scrolls every scrollable ancestor
 * (the dashboard frame and the page included).
 */
export function centreActiveTab(root) {
  const scroller = root?.querySelector('.MuiTabs-scroller');
  if (!scroller) return;
  const tab = scroller.querySelector('[role="tab"][aria-selected="true"]');
  if (tab && scroller.scrollWidth - scroller.clientWidth > 1) {
    // offsetLeft is relative to the flex container; measure against the scroller instead.
    const left = tab.getBoundingClientRect().left - scroller.getBoundingClientRect().left + scroller.scrollLeft;
    const target = Math.max(0, Math.min(scroller.scrollWidth - scroller.clientWidth, left - (scroller.clientWidth - tab.offsetWidth) / 2));
    if (typeof scroller.scrollTo === 'function') scroller.scrollTo({ left: target });
    else scroller.scrollLeft = target;
  }
  markTabFade(scroller);
}

/**
 * Keeps the selected tab fully visible, centred in its scroller, on mount,
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
      observer = new ResizeObserver(run);
      observer.observe(scroller);
      const strip = scroller.firstElementChild;
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

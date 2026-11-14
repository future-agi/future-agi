import { useEffect } from 'react';

// Shared behaviour for Skills tab strips (primary navigation and object tabs).
// - The theme gives scroll buttons 48px; a disabled start button kept that
//   space and indented the first tab ~50px at 390px. Disabled buttons now
//   collapse, so the first tab starts on the route gutter.
// - Buttons stay available on phones (pass allowScrollButtonsMobile).
export const scrollTabsSx = {
  minWidth: 0,
  '& .MuiTabs-scrollButtons': { width: 32, flexShrink: 0 },
  '& .MuiTabs-scrollButtons.Mui-disabled': { display: 'none' },
};

// Props every Skills tab strip uses so later tabs are reachable on a phone.
export const scrollTabsProps = { variant: 'scrollable', scrollButtons: 'auto', allowScrollButtonsMobile: true };

/**
 * Keeps the selected tab fully visible inside its scroller, on mount and
 * whenever the selection changes. `ref` points at the Tabs root or any
 * ancestor that contains exactly one tab strip.
 */
export function useActiveTabInView(ref, value) {
  useEffect(() => {
    const root = ref.current;
    if (!root || typeof window === 'undefined') return undefined;
    const frame = window.requestAnimationFrame(() => {
      const scroller = root.querySelector('.MuiTabs-scroller');
      const tab = root.querySelector('[role="tab"][aria-selected="true"]');
      if (!scroller || !tab) return;
      const box = scroller.getBoundingClientRect();
      const target = tab.getBoundingClientRect();
      const gap = 16;
      if (target.left < box.left) scroller.scrollLeft -= box.left - target.left + gap;
      else if (target.right > box.right) scroller.scrollLeft += target.right - box.right + gap;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [ref, value]);
}

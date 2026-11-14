import { useEffect } from 'react';

// Sticky task footers publish their height so the floating assistant sits above
// them instead of over their actions: --skills-sticky-footer-height on <html>,
// the tallest mounted footer wins, and the property is removed with the last.
const footerHeights = new Map();
const FOOTER_VAR = '--skills-sticky-footer-height';
function publishFooterHeight() {
  if (typeof document === 'undefined') return;
  const height = Math.max(0, ...footerHeights.values());
  if (footerHeights.size && height > 0) document.documentElement.style.setProperty(FOOTER_VAR, `${Math.ceil(height)}px`);
  else document.documentElement.style.removeProperty(FOOTER_VAR);
}
/** Call with the ref of a `position: sticky; bottom: 0` footer that carries `data-sticky-footer`. */
export function useStickyFooterHeight(ref) {
  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const key = {};
    const measure = () => { footerHeights.set(key, node.getBoundingClientRect().height); publishFooterHeight(); };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(node);
    return () => { observer?.disconnect(); footerHeights.delete(key); publishFooterHeight(); };
  }, [ref]);
}

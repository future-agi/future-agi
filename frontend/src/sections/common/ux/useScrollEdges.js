// Scroll affordance for a horizontally scrolling grid (R3 analysis fix): is
// there more content to the left / right of what is visible? Drives the
// fades a table shows at its clipped edges, so a cut column reads as
// "scroll for more", never as a clipped page.
import { useEffect, useState } from "react";

/** `{left, right}` for a scroller's position (pure; 1 px tolerance). */
export function scrollEdges({
  scrollLeft = 0,
  scrollWidth = 0,
  clientWidth = 0,
} = {}) {
  return {
    left: scrollLeft > 1,
    right: scrollLeft + clientWidth < scrollWidth - 1,
  };
}

/**
 * Watches the scroller `selector` inside `ref` (e.g. AG Grid's
 * `.ag-center-cols-viewport`) and returns `{left, right}`. Re-checks on
 * scroll, resize and when the grid re-renders its columns.
 * @param {React.RefObject<HTMLElement>} ref  container
 * @param {string} selector  the scrolling element inside it
 * @param {Array} [deps]  re-attach when these change (columns, view)
 */
export default function useScrollEdges(ref, selector, deps = []) {
  const [edges, setEdges] = useState({ left: false, right: false });
  useEffect(() => {
    const root = ref.current;
    if (!root) return undefined;
    let el = null;
    let ro = null;
    const update = () => {
      if (!el) return;
      const next = scrollEdges(el);
      setEdges((prev) =>
        prev.left === next.left && prev.right === next.right ? prev : next,
      );
    };
    const attach = () => {
      const found = root.querySelector(selector);
      if (!found || found === el) return;
      el?.removeEventListener("scroll", update);
      el = found;
      el.addEventListener("scroll", update, { passive: true });
      ro?.disconnect();
      ro = new ResizeObserver(update);
      ro.observe(el);
      const inner = el.firstElementChild;
      if (inner) ro.observe(inner);
      update();
    };
    attach();
    // AG Grid mounts its viewport after the first render and swaps column
    // containers when columns change: re-attach on DOM changes.
    const mo = new MutationObserver(() => {
      attach();
      update();
    });
    mo.observe(root, { childList: true, subtree: true });
    return () => {
      mo.disconnect();
      ro?.disconnect();
      el?.removeEventListener("scroll", update);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ref, selector, ...deps]);
  return edges;
}

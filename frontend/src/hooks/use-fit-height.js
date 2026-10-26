import { useLayoutEffect, useState } from "react";

/**
 * The height that lets an element reach the bottom of the window from where
 * it actually starts — so a header or chart above it can change height
 * without pushing what's at its bottom (pagination) off screen.
 * Returns null until measured; pair it with a CSS fallback.
 */
export default function useFitHeight(ref, { gap = 16, min = 320 } = {}) {
  const [height, setHeight] = useState(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const fit = () => {
      const top = el.getBoundingClientRect().top;
      setHeight(Math.max(min, Math.round(window.innerHeight - top - gap)));
    };
    fit();
    window.addEventListener("resize", fit);
    /* The header or chart above can grow or shrink after load. */
    const ro =
      typeof ResizeObserver !== "undefined" && el.parentElement
        ? new ResizeObserver(fit)
        : null;
    ro?.observe(el.parentElement);
    return () => {
      window.removeEventListener("resize", fit);
      ro?.disconnect();
    };
  }, [ref, gap, min]);
  return height;
}

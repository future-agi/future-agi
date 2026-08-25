import { useCallback, useEffect, useRef, useState } from "react";

/** Reports whether a single-line element is clipping its own text, so callers
 *  can offer a tooltip only when there is hidden text to reveal. */
export default function useIsTruncated(text) {
  const ref = useRef(null);
  const [isTruncated, setIsTruncated] = useState(false);

  const measure = useCallback(() => {
    const el = ref.current;
    if (el) setIsTruncated(el.scrollWidth > el.clientWidth);
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [measure, text]);

  return [ref, isTruncated];
}

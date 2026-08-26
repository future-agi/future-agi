import { useCallback, useEffect, useState } from "react";

/** Reports whether a single-line element is clipping its own text, so callers
 *  can offer a tooltip only when there is hidden text to reveal.
 *
 *  The measured node is held in state and handed back as a callback ref, not a
 *  ref object: revealing the tooltip swaps the wrapper around the text, which
 *  remounts the node underneath it. A ref object would leave the observer
 *  watching the detached node and freeze the result at whatever it read first. */
export default function useIsTruncated(text) {
  const [node, setNode] = useState(null);
  const [isTruncated, setIsTruncated] = useState(false);

  const measure = useCallback(() => {
    if (node) setIsTruncated(node.scrollWidth > node.clientWidth);
  }, [node]);

  useEffect(() => {
    if (!node) return undefined;

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [measure, node, text]);

  return [setNode, isTruncated];
}

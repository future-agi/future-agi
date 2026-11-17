// HO results R2 review fix: does a control fit in its toolbar row? The row
// is measured, never the viewport: its free room is what its flexible parts
// (the right-hand action bar, a selection bar: `flex: 1`, content pushed to
// one end) leave beside their own content; a row without one leaves what
// its children do not take.
import { useCallback, useEffect, useRef, useState } from "react";

/**
 * The px a row leaves free; negative when its parts need more than it has
 * (a flexible part squeezed below its content, e.g. an action bar whose
 * pinned buttons spill under its neighbour). Pure over boxes.
 * @param {{width: number, gap?: number, kids: {width: number, grow?:
 *   boolean, content?: number}[]}} row `content` = a flexible child's
 *   content width
 * @returns {number}
 */
export function rowSlack({ width, gap = 0, kids = [] }) {
  const used = kids.reduce((s, k) => s + k.width, 0);
  const outside = width - used - gap * Math.max(0, kids.length - 1);
  const inside = kids
    .filter((k) => k.grow)
    .reduce((s, k) => s + (k.width - (k.content ?? k.width)), 0);
  return outside + inside;
}

/**
 * Pure: the control fits when the room it has (the row's slack plus what it
 * takes now, 0 when it is not shown) holds its full width. Counting its own
 * width keeps the answer still once it shows.
 * @param {{slack: number, shown: number, need: number}} m
 */
export const fitsRow = ({ slack, shown = 0, need }) =>
  Number.isFinite(slack) && Number.isFinite(need) && slack + shown >= need;

const outOfFlow = (s) =>
  s.display === "none" ||
  s.visibility === "hidden" ||
  s.position === "absolute" ||
  s.position === "fixed";

const inFlowKids = (el) =>
  Array.from(el.children)
    .filter((c) => !outOfFlow(getComputedStyle(c)))
    .map((c) => ({ el: c, width: c.getBoundingClientRect().width }))
    .filter((k) => k.width > 0);

const grows = (el) => Number.parseFloat(getComputedStyle(el).flexGrow) > 0;
const gapOf = (el) => Number.parseFloat(getComputedStyle(el).columnGap) || 0;

/**
 * The width an element's content needs: in a flex row, its fixed children
 * plus what its growing children need, and the gaps; in a block, its widest
 * child (a full-width inner row: what that row needs).
 */
export function contentWidthOf(el, depth = 0) {
  const width = el.getBoundingClientRect().width;
  const kids = inFlowKids(el);
  if (!kids.length || depth > 6) return width;
  const s = getComputedStyle(el);
  if (s.display.includes("flex") && !s.flexDirection.startsWith("column"))
    return (
      kids.reduce(
        (sum, k) =>
          sum + (grows(k.el) ? contentWidthOf(k.el, depth + 1) : k.width),
        0,
      ) +
      gapOf(el) * (kids.length - 1)
    );
  return Math.max(
    ...kids.map((k) =>
      k.width >= width - 0.5 ? contentWidthOf(k.el, depth + 1) : k.width,
    ),
  );
}

/** The row's boxes, read from the DOM (for rowSlack). */
export function measureRow(row) {
  return {
    width: row.getBoundingClientRect().width,
    gap: gapOf(row),
    kids: inFlowKids(row).map(({ el, width }) =>
      grows(el)
        ? { width, grow: true, content: contentWidthOf(el) }
        : { width },
    ),
  };
}

/**
 * Whether a control fits its row: `rowOf()` the row element, `slotRef` the
 * control's own element when shown (null when not), `twinRef` a hidden copy
 * of it at full width. Re-measured when the row or its parts resize or
 * change. Without a row it fits (nothing to measure against).
 * @returns {boolean|null} null until measured
 */
export function useRowFit({ rowOf, slotRef, twinRef }) {
  const [fits, setFits] = useState(rowOf ? null : true);
  const rowOfRef = useRef(rowOf);
  rowOfRef.current = rowOf;
  const measure = useCallback(() => {
    const row = rowOfRef.current?.();
    const twin = twinRef.current;
    if (!row || !twin) return;
    // The gap the control adds beside its neighbours (its toolbar's).
    const parentGap = (el) => (el?.parentElement ? gapOf(el.parentElement) : 0);
    const slot = slotRef.current;
    const shown = slot
      ? slot.getBoundingClientRect().width + parentGap(slot)
      : 0;
    const need = twin.getBoundingClientRect().width + parentGap(twin);
    setFits(fitsRow({ slack: rowSlack(measureRow(row)), shown, need }));
  }, [slotRef, twinRef]);

  // After the commit (a passive effect): the row is an ancestor, whose ref
  // is attached only after this component's own layout effects ran.
  useEffect(() => {
    const row = rowOfRef.current?.();
    if (!row) return undefined;
    measure();
    let frame = 0;
    const ro =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => {
            cancelAnimationFrame(frame);
            frame = requestAnimationFrame(measure);
          });
    // The row and each of its parts (a part that comes later, e.g. the
    // selection bar, is observed once it is there).
    const observe = () => {
      ro?.disconnect();
      ro?.observe(row);
      Array.from(row.children).forEach((c) => ro?.observe(c));
    };
    observe();
    const later = () => {
      observe();
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    };
    const mo =
      typeof MutationObserver === "undefined"
        ? null
        : new MutationObserver(later);
    mo?.observe(row, { childList: true, subtree: true });
    window.addEventListener("resize", later);
    return () => {
      cancelAnimationFrame(frame);
      ro?.disconnect();
      mo?.disconnect();
      window.removeEventListener("resize", later);
    };
  }, [measure]);

  return fits;
}

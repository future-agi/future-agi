// SW5-32: keyboard access to the span rows of the trace tree and the trace
// timeline (the WAI-ARIA tree pattern). One row is the Tab stop (roving
// tabindex); ArrowDown / ArrowUp move focus, Enter / Space select the span,
// ArrowRight / ArrowLeft expand and collapse it.

const nameMatches = (name, q) => name.toLowerCase().includes(q);

// Same rule as the rows' own search filter: a row stays when its name
// matches or a descendant's name does.
const subtreeMatches = (children, q) =>
  (children || []).some(
    (child) =>
      nameMatches(child?.observation_span?.name || "", q) ||
      subtreeMatches(child?.children, q),
  );

/**
 * Ids of the span rows on screen, in order: a collapsed row's children are
 * unmounted, and a search hides rows that neither match nor lead to a match.
 * `rootLabel` is a caller's name for the root rows (TraceTreeV2 rowExtras).
 */
export function shownRowIds(entries, expandedSet, searchQuery, rootLabel) {
  const q = (searchQuery || "").toLowerCase();
  const ids = [];
  const walk = (list, depth) => {
    for (const entry of list || []) {
      const span = entry?.observation_span || {};
      const name = (depth === 0 && rootLabel) || span.name || "unnamed";
      if (q && !nameMatches(name, q) && !subtreeMatches(entry?.children, q))
        continue;
      ids.push(span.id);
      if (entry?.children?.length && expandedSet.has(span.id))
        walk(entry.children, depth + 1);
    }
  };
  walk(entries, 0);
  return ids;
}

/** The row Tab lands on: the selected span's when shown, else the first. */
export function tabStopRowId(
  entries,
  expandedSet,
  searchQuery,
  selectedSpanId,
  rootLabel,
) {
  const ids = shownRowIds(entries, expandedSet, searchQuery, rootLabel);
  return ids.includes(selectedSpanId) ? selectedSpanId : ids[0];
}

/**
 * Keydown on a span row (`role="treeitem"` inside a `role="tree"`). Keys
 * pressed on a control inside the row (the chevron) stay with that control,
 * and modified keys stay with the browser.
 */
export function onSpanRowKeyDown(
  e,
  { spanId, hasChildren, isExpanded, onSelect, onToggleExpand },
) {
  if (e.target !== e.currentTarget || e.altKey || e.ctrlKey || e.metaKey)
    return;
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    onSelect(spanId);
  } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const tree = e.currentTarget.closest('[role="tree"]');
    const rows = tree
      ? Array.from(tree.querySelectorAll('[role="treeitem"]'))
      : [];
    const at = rows.indexOf(e.currentTarget);
    rows[e.key === "ArrowDown" ? at + 1 : at - 1]?.focus();
  } else if (e.key === "ArrowRight" && hasChildren && !isExpanded) {
    e.preventDefault();
    onToggleExpand(spanId);
  } else if (e.key === "ArrowLeft" && hasChildren && isExpanded) {
    e.preventDefault();
    onToggleExpand(spanId);
  }
}

// Visible keyboard focus on a row inside an `overflow: auto` scroller.
export const ROW_FOCUS_SX = {
  "&:focus-visible": {
    outline: "2px solid",
    outlineColor: "primary.main",
    outlineOffset: "-2px",
  },
};

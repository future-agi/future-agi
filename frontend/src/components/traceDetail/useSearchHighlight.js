import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Find-in-page for the trace span Preview.
 *
 * Implemented with the CSS Custom Highlight API: we build Range objects
 * over the live DOM and hand them to CSS.highlights. Nothing inside
 * `containerRef` is mutated — only the browser's highlight layer
 * changes — so React's reconciler never fights over the tree.
 *
 * Contract:
 *   - Any ancestor element carrying `data-search-skip` is ignored by
 *     the walker (use it for UI chrome like table headers so column
 *     labels don't match the query).
 *   - Active match gets its own highlight name so CSS can style it
 *     distinctly from the rest.
 *   - Scroll happens ONLY when the user's intent changed (new query or
 *     prev/next click). Observer-driven re-applies (triggered by DOM
 *     churn like AttributesCard's row filter) restore the active
 *     highlight in place but never yank the viewport.
 */

const HIGHLIGHT_NAME = "trace-search";
const HIGHLIGHT_NAME_ACTIVE = "trace-search-active";
const STYLE_ELEMENT_ID = "trace-search-highlight-styles";

const SKIP_TAGS = new Set([
  "SCRIPT",
  "STYLE",
  "NOSCRIPT",
  "INPUT",
  "TEXTAREA",
  "SELECT",
  "OPTION",
]);

function highlightsSupported() {
  return (
    typeof CSS !== "undefined" &&
    typeof CSS.highlights !== "undefined" &&
    typeof window !== "undefined" &&
    typeof window.Highlight !== "undefined"
  );
}

function ensureGlobalStyles() {
  if (typeof document === "undefined") return;
  const css = `
    ::highlight(${HIGHLIGHT_NAME}) {
      background-color: #ffe082;
      color: #1a1a1a;
    }
    ::highlight(${HIGHLIGHT_NAME_ACTIVE}) {
      background-color: #ff6d00;
      color: #ffffff;
    }
  `;
  let style = document.getElementById(STYLE_ELEMENT_ID);
  if (!style) {
    style = document.createElement("style");
    style.id = STYLE_ELEMENT_ID;
    document.head.appendChild(style);
  }
  if (style.textContent !== css) style.textContent = css;
}

function getOrCreateHighlight(name) {
  let h = CSS.highlights.get(name);
  if (!h) {
    h = new window.Highlight();
    CSS.highlights.set(name, h);
  }
  return h;
}

// Walk every scrollable ancestor of the range's parent element up to
// the document root and center the *match itself* within each
// container's visible area.
//
// Critical: we use `range.getBoundingClientRect()` rather than the
// parent element's rect. In Markdown content the whole blob is rendered
// into a single long `<p>`, so the parent rect is the entire paragraph
// (hundreds of px tall) — centering the paragraph puts the match nowhere
// near the viewport center. Range rect gives us the exact match bounds.
//
// Synchronous `scrollTop +=` so the next ancestor sees an up-to-date
// bounding rect — `scrollBy` with smooth animation races across nested
// scrollers. Respects `scroll-padding-top` / `-bottom` so a pane with a
// sticky header doesn't park the active match behind it.
function scrollRangeIntoView(range) {
  if (!range) return;
  const startNode = range.startContainer;
  const startEl =
    startNode.nodeType === Node.TEXT_NODE ? startNode.parentElement : startNode;
  if (!startEl || !startEl.isConnected) return;

  let current = startEl.parentElement;
  while (
    current &&
    current !== document.documentElement &&
    current !== document.body
  ) {
    // Any element with more content than fits is programmatically
    // scrollable via `scrollTop += …`, regardless of computed overflow.
    const isScrollable = current.scrollHeight > current.clientHeight + 1;
    if (isScrollable) {
      const style = window.getComputedStyle(current);
      const paddingTop = parseFloat(style.scrollPaddingTop) || 0;
      const paddingBottom = parseFloat(style.scrollPaddingBottom) || 0;
      const containerRect = current.getBoundingClientRect();
      const matchRect = range.getBoundingClientRect();
      // A collapsed / zero-size range gives a zero rect — skip; the
      // outer walk iterations will still try on the next scrollable.
      if (matchRect.width > 0 || matchRect.height > 0) {
        const visibleTop = containerRect.top + paddingTop;
        const visibleBottom = containerRect.bottom - paddingBottom;
        const matchTop = matchRect.top;
        const matchBottom = matchRect.bottom;
        // Only scroll if the match isn't already fully visible inside
        // this container. Centering a match that's already in view just
        // yanks the surrounding content (e.g. a top Error banner) out
        // of sight for no benefit.
        const isFullyVisible =
          matchTop >= visibleTop && matchBottom <= visibleBottom;
        if (!isFullyVisible) {
          const visibleHeight = visibleBottom - visibleTop;
          const containerCenter = visibleTop + visibleHeight / 2;
          const matchCenter = matchTop + matchRect.height / 2;
          const delta = matchCenter - containerCenter;
          if (Math.abs(delta) >= 1) {
            current.scrollTop += delta;
          }
        }
      }
    }
    current = current.parentElement;
  }
}

function collectTextNodes(root) {
  const nodes = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !node.nodeValue.trim()) {
        return NodeFilter.FILTER_REJECT;
      }
      let parent = node.parentElement;
      while (parent && parent !== root) {
        if (SKIP_TAGS.has(parent.tagName)) return NodeFilter.FILTER_REJECT;
        if (parent.hasAttribute?.("data-search-skip")) {
          return NodeFilter.FILTER_REJECT;
        }
        parent = parent.parentElement;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  let n = walker.nextNode();
  while (n) {
    nodes.push(n);
    n = walker.nextNode();
  }
  return nodes;
}

export default function useSearchHighlight(containerRef, query) {
  const [matchCount, setMatchCount] = useState(0);
  const [activeIndex, setActiveIndex] = useState(0);
  const rangesRef = useRef([]);
  const activeIndexRef = useRef(0);
  const lastQueryRef = useRef("");

  // Mirror activeIndex into a ref so observer-driven apply() calls can
  // restore the correct active highlight without a stale closure.
  useEffect(() => {
    activeIndexRef.current = activeIndex;
  }, [activeIndex]);

  // Schedule the scroll for the next animation frame so any layout
  // changes caused by the same user action have committed first.
  const scrollToActive = useCallback(() => {
    requestAnimationFrame(() => {
      const ranges = rangesRef.current;
      if (!ranges.length) return;
      const idx = Math.min(activeIndexRef.current, ranges.length - 1);
      const target = ranges[idx];
      if (!target) return;
      scrollRangeIntoView(target);
    });
  }, []);

  // Main effect: on query change, build the match set + install a
  // MutationObserver. Observer keeps the highlight in sync with DOM
  // churn (AttributesCard row filter, collapse/expand) WITHOUT
  // triggering a scroll — only user intent moves the viewport.
  useEffect(() => {
    if (!highlightsSupported()) return undefined;
    ensureGlobalStyles();
    const root = containerRef.current;
    if (!root) return undefined;

    const q = (query || "").trim();
    const queryChanged = q !== lastQueryRef.current;
    lastQueryRef.current = q;
    if (queryChanged) {
      activeIndexRef.current = 0;
      setActiveIndex(0);
    }

    const highlight = getOrCreateHighlight(HIGHLIGHT_NAME);
    const activeHighlight = getOrCreateHighlight(HIGHLIGHT_NAME_ACTIVE);

    const apply = () => {
      // Pause observation so our own highlight changes don't re-fire it.
      observer.disconnect();
      highlight.clear();
      activeHighlight.clear();
      if (!q) {
        rangesRef.current = [];
        setMatchCount(0);
      } else {
        const qLower = q.toLowerCase();
        const textNodes = collectTextNodes(root);
        const ranges = [];
        textNodes.forEach((tn) => {
          const value = tn.nodeValue;
          const lower = value.toLowerCase();
          let idx = lower.indexOf(qLower);
          while (idx !== -1) {
            const range = document.createRange();
            range.setStart(tn, idx);
            range.setEnd(tn, idx + qLower.length);
            highlight.add(range);
            ranges.push(range);
            idx = lower.indexOf(qLower, idx + qLower.length);
          }
        });
        rangesRef.current = ranges;
        setMatchCount(ranges.length);
        // Restore the active highlight from the current activeIndex so
        // observer-driven rebuilds don't silently drop it.
        if (ranges.length) {
          const idx = Math.min(activeIndexRef.current, ranges.length - 1);
          const target = ranges[idx];
          if (target) activeHighlight.add(target);
        }
      }
      observer.observe(root, {
        childList: true,
        subtree: true,
        characterData: true,
      });
    };

    let scheduled = 0;
    const observer = new MutationObserver(() => {
      // Coalesce bursts (a row-filter reflow can emit dozens of records).
      if (scheduled) return;
      scheduled = requestAnimationFrame(() => {
        scheduled = 0;
        apply();
      });
    });

    apply();

    // Scroll only when the user actually changed the query — not for
    // observer-driven rebuilds downstream of AttributesCard filtering.
    if (queryChanged && q) scrollToActive();

    return () => {
      if (scheduled) cancelAnimationFrame(scheduled);
      observer.disconnect();
      highlight.clear();
      activeHighlight.clear();
      rangesRef.current = [];
    };
  }, [containerRef, query, scrollToActive]);

  // Prev/next: promote the new range and scroll it into view.
  useEffect(() => {
    if (!highlightsSupported()) return;
    const ranges = rangesRef.current;
    if (!ranges.length) return;
    const activeHighlight = getOrCreateHighlight(HIGHLIGHT_NAME_ACTIVE);
    activeHighlight.clear();
    const idx = Math.min(activeIndex, ranges.length - 1);
    const target = ranges[idx];
    if (!target) return;
    activeHighlight.add(target);
    scrollToActive();
  }, [activeIndex, scrollToActive]);

  const next = () => {
    if (!matchCount) return;
    setActiveIndex((i) => (i + 1) % matchCount);
  };
  const prev = () => {
    if (!matchCount) return;
    setActiveIndex((i) => (i - 1 + matchCount) % matchCount);
  };

  return { matchCount, activeIndex, setActiveIndex, next, prev };
}

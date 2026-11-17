import React, { useCallback, useLayoutEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

import Scrollbar from "src/components/scrollbar";

// The gateway rail's scroll area. At common laptop heights (1440x960) the
// rail is a little taller than the room between the header and the
// Resources footer, and the scrollbar only shows on hover, so the last items
// sat cut off with nothing saying the rail scrolls. While more items are
// below, the bottom 40 px fade out (an alpha mask: no colour, so it works in
// light and dark) and a chevron sits on the fade at the rail's right edge,
// clear of the item labels; at the end of the rail both go away. 40 px is
// taller than the gap between two items, so the fade always covers part of
// one (at 1024x768 a 16 px fade fell on a gap and the rail looked complete).
// A click on the chevron scrolls the rail down. The current page's item is
// kept in view: on load and on every route change the rail scrolls to it
// (otherwise Webhooks or Gateway settings sat below the fold at 1024x768).

const FADE = "linear-gradient(to bottom, black calc(100% - 40px), transparent)";

// No padding under the rail: at 1440x960 it fits with none
// (GatewayRail.fit.test.jsx).
const SCROLLBAR_SX = {
  height: 1,
  // Scrolled into view, the current item stops above the fade and chevron.
  "& [aria-current='page']": { scrollMarginTop: 8, scrollMarginBottom: 48 },
  "& .simplebar-content": {
    height: "auto",
    minHeight: 1,
    display: "flex",
    flexDirection: "column",
    paddingBottom: 0,
  },
};

// Holds the masked area and, outside the mask, the chevron.
const FRAME_SX = {
  flex: 1,
  minHeight: 0,
  position: "relative",
  display: "flex",
  flexDirection: "column",
};

// Out of the tab order and hidden from assistive tech: the items below are
// reached by Tab like the rest, and the rail scrolls to each.
const MORE_SX = {
  position: "absolute",
  bottom: 4,
  right: 12,
  width: 24,
  height: 24,
  p: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  border: 1,
  borderColor: "divider",
  borderRadius: "50%",
  bgcolor: "background.paper",
  color: "text.secondary",
  cursor: "pointer",
  "&:hover": { color: "text.primary" },
};

function useMoreBelow(ref) {
  const [more, setMore] = useState(false);
  const update = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    setMore(el.scrollHeight - el.scrollTop - el.clientHeight > 1);
  }, [ref]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    el.dataset.railScroller = "";
    update();
    el.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    // The rail's own height changes too (a role-gated item appears).
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(el);
    if (el.firstElementChild) observer?.observe(el.firstElementChild);
    return () => {
      el.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      observer?.disconnect();
    };
  }, [ref, update]);

  return more;
}

// Scrolls the current page's item (aria-current="page", set by GatewayRail)
// into view inside the rail, on mount and whenever another item becomes the
// current one (a route change). It watches the rail rather than the router,
// so it works wherever the rail is mounted; an item already in view does not
// move, and the user's own scrolling is left alone until the page changes.
function useCurrentItemInView(ref) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    let shown = null;
    const reveal = () => {
      const item = el.querySelector("[aria-current='page']");
      if (!item || item === shown) return;
      shown = item;
      item.scrollIntoView?.({ block: "nearest" });
    };
    reveal();
    if (typeof MutationObserver === "undefined") return undefined;
    const observer = new MutationObserver(reveal);
    observer.observe(el, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["aria-current"],
    });
    return () => observer.disconnect();
  }, [ref]);
}

export default function GatewayRailScroll({ children }) {
  const ref = useRef(null);
  const more = useMoreBelow(ref);
  useCurrentItemInView(ref);
  // Most of a screen, so the next items come into view with some of the
  // current ones kept for context.
  const scrollDown = () => {
    const el = ref.current;
    if (!el) return;
    const top = Math.max(el.clientHeight - 64, 64);
    if (typeof el.scrollBy === "function") {
      el.scrollBy({ top, behavior: "smooth" });
    } else {
      el.scrollTop += top;
    }
  };
  return (
    <Box sx={FRAME_SX}>
      <Box
        data-testid="gateway-rail-scroll"
        data-more-below={more ? "true" : "false"}
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: "hidden",
          ...(more && { maskImage: FADE, WebkitMaskImage: FADE }),
        }}
      >
        <Scrollbar ref={ref} sx={SCROLLBAR_SX}>
          {children}
        </Scrollbar>
      </Box>
      {more && (
        <Box
          component="button"
          type="button"
          tabIndex={-1}
          aria-hidden
          data-testid="gateway-rail-more"
          onClick={scrollDown}
          sx={MORE_SX}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="m6 9 6 6 6-6" />
          </svg>
        </Box>
      )}
    </Box>
  );
}

GatewayRailScroll.propTypes = { children: PropTypes.node };

import React, { useCallback, useLayoutEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

import Scrollbar from "src/components/scrollbar";

// The gateway rail's scroll area. At common laptop heights (1440x960) the
// rail is a little taller than the room between the header and the
// Resources footer, and the scrollbar only shows on hover, so the last items
// sat cut off with nothing saying the rail scrolls. While more items are
// below, the bottom 16 px fade out (an alpha mask: no colour, so it works in
// light and dark); at the end of the rail the fade goes away.

const FADE = "linear-gradient(to bottom, black calc(100% - 16px), transparent)";

// No padding under the rail: at 1440x960 it fits with none
// (GatewayRail.fit.test.jsx).
const SCROLLBAR_SX = {
  height: 1,
  "& .simplebar-content": {
    height: "auto",
    minHeight: 1,
    display: "flex",
    flexDirection: "column",
    paddingBottom: 0,
  },
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

export default function GatewayRailScroll({ children }) {
  const ref = useRef(null);
  const more = useMoreBelow(ref);
  return (
    <Box
      data-testid="gateway-rail-scroll"
      data-more-below={more ? "true" : "false"}
      sx={{
        flex: 1,
        overflow: "hidden",
        ...(more && { maskImage: FADE, WebkitMaskImage: FADE }),
      }}
    >
      <Scrollbar ref={ref} sx={SCROLLBAR_SX}>
        {children}
      </Scrollbar>
    </Box>
  );
}

GatewayRailScroll.propTypes = { children: PropTypes.node };

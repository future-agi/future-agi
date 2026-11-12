import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import { isGatewayPath } from "src/sections/omega/shell/products";
import { GatewayNavigationPath } from "../context/GatewayNavigationPath";

/** Focus only intentional keyboard page changes, never every frame render. */
export default function GatewayNavigationFocus({ pathname, children }) {
  const currentPath = useRef(pathname);
  const previousPath = useRef(pathname);
  const intent = useRef(null);
  const cancelPending = useRef(() => {});
  const [announcement, setAnnouncement] = useState("");
  currentPath.current = pathname;

  useEffect(() => {
    const keyboard = (event) => {
      cancelPending.current();
      const target = event.target;
      const railLink = target.closest?.('nav[aria-label="AI Gateway"] a[href]');
      const palette = target.closest?.("[data-dsx-command-palette]");
      const activatesPage =
        event.key === "Enter" &&
        !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey &&
        (railLink || palette);
      intent.current = activatesPage
        ? { pathname: currentPath.current, at: Date.now() }
        : null;
    };
    const pointer = () => {
      intent.current = null;
      cancelPending.current();
    };
    document.addEventListener("keydown", keyboard, true);
    document.addEventListener("pointerdown", pointer, true);
    return () => {
      document.removeEventListener("keydown", keyboard, true);
      document.removeEventListener("pointerdown", pointer, true);
      cancelPending.current();
    };
  }, []);

  useEffect(() => {
    const from = previousPath.current;
    previousPath.current = pathname;
    if (from === pathname) return undefined;
    const requested = intent.current;
    intent.current = null;
    setAnnouncement("");
    // Palette execution is deferred until its overlay has restored focus.
    // An old, non-navigation command must not capture a later route change.
    if (
      !isGatewayPath(pathname) ||
      requested?.pathname !== from ||
      Date.now() - requested.at > 2000
    ) return undefined;

    let firstFrame;
    let secondFrame;
    let stopped = false;
    const stop = () => {
      stopped = true;
      observer.disconnect();
      cancelAnimationFrame(firstFrame);
      cancelAnimationFrame(secondFrame);
    };
    const findHeading = () => {
      if (stopped) return;
      const heading = Array.from(
        document.querySelectorAll("#main-content [data-gateway-page-path]"),
      ).find((node) => node.dataset.gatewayPagePath === pathname);
      if (!heading) return;
      observer.disconnect();
      // The temporary drawer closes on the same route commit. Let its focus
      // restoration finish before selecting the new page, including lazy pages.
      firstFrame = requestAnimationFrame(() => {
        secondFrame = requestAnimationFrame(() => {
          if (
            stopped || !heading.isConnected ||
            document.activeElement?.closest('[role="dialog"]')
          ) return;
          heading.focus();
          setAnnouncement(heading.textContent || "AI Gateway");
        });
      });
    };
    const observer = new MutationObserver(findHeading);
    cancelPending.current = stop;
    observer.observe(document.getElementById("main-content") || document.body, {
      childList: true,
      subtree: true,
      attributes: true,
    });
    findHeading();
    return stop;
  }, [pathname]);

  return (
    <GatewayNavigationPath.Provider value={pathname}>
      {children}
      <Box
        role="status"
        aria-live="polite"
        aria-atomic="true"
        aria-label="Page navigation"
        sx={{
          position: "absolute",
          width: "1px",
          height: "1px",
          p: 0,
          overflow: "hidden",
          clip: "rect(0 0 0 0)",
          whiteSpace: "nowrap",
        }}
      >
        {announcement}
      </Box>
    </GatewayNavigationPath.Provider>
  );
}

GatewayNavigationFocus.propTypes = {
  pathname: PropTypes.string.isRequired,
  children: PropTypes.node,
};

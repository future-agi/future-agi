import React, { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import Fab from "@mui/material/Fab";
import SvgIcon from "@mui/material/SvgIcon";
import { useTheme } from "@mui/material/styles";
import useMediaQuery from "@mui/material/useMediaQuery";
import { paths } from "src/routes/paths";
import CustomTooltip from "src/components/tooltip";
import useFalconStore from "../store/useFalconStore";
import { useFeatureAllowed } from "src/hooks/useCapabilities";

function FalconIcon(props) {
  return (
    <SvgIcon {...props} viewBox="0 0 24 24">
      {/* Falcon spaceship — pointing UP, geometric, matches brand */}
      <g transform="rotate(180, 12, 12)">
        <path
          d="M12 3L8 10L7 17L12 20L17 17L16 10Z"
          fill="currentColor"
          opacity="0.25"
        />
        <path
          d="M12 3L8 10L7 17L12 20L17 17L16 10Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        <path
          d="M10.5 9L12 6.5L13.5 9"
          fill="currentColor"
          opacity="0.4"
          stroke="currentColor"
          strokeWidth="1"
          strokeLinejoin="round"
        />
        <path
          d="M8 10L4 8.5V12L7 14"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        <path
          d="M16 10L20 8.5V12L17 14"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        <path
          d="M9.5 18L12 21.5L14.5 18"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity="0.6"
        />
      </g>
    </SvgIcon>
  );
}

// Skills task footers mark themselves with [data-sticky-footer] and publish
// their height as --skills-sticky-footer-height (useStickyFooterHeight in
// components/stickyFooter). The FAB also measures any marked footer that is on
// screen, so it clears footers that have not adopted the hook yet. It reports
// whether a modal surface (dialog, drawer, menu) is open, because on a phone
// the FAB steps aside for those and for footers instead of covering them.
const OVERLAY = '.MuiModal-root:not(.MuiModal-hidden), [role="dialog"]';
function useSkillsSurfaces(active) {
  const [surfaces, setSurfaces] = useState({ footer: false, footerHeight: 0, overlay: false });
  useEffect(() => {
    if (!active || typeof MutationObserver === "undefined") {
      setSurfaces({ footer: false, footerHeight: 0, overlay: false });
      return undefined;
    }
    let frame = 0;
    const sizes = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => schedule());
    const watched = new Set();
    const measure = () => {
      const footers = [...document.querySelectorAll("[data-sticky-footer]")].filter((node) => !node.closest(".MuiModal-root"));
      footers.forEach((node) => { if (!watched.has(node)) { watched.add(node); sizes?.observe(node); } });
      const viewport = window.innerHeight;
      const footerHeight = footers.reduce((height, node) => {
        const rect = node.getBoundingClientRect();
        // Only a footer that is on screen and reaches the bottom edge lifts the FAB.
        return rect.height > 0 && rect.top < viewport && rect.bottom >= viewport - 24 ? Math.max(height, Math.ceil(viewport - rect.top)) : height;
      }, 0);
      const next = { footer: footers.length > 0, footerHeight, overlay: Boolean(document.querySelector(OVERLAY)) };
      setSurfaces((current) => (current.footer === next.footer && current.footerHeight === next.footerHeight && current.overlay === next.overlay ? current : next));
    };
    function schedule() {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(measure);
    }
    measure();
    const observer = new MutationObserver(schedule);
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-sticky-footer", "class"] });
    window.addEventListener("resize", schedule);
    document.addEventListener("scroll", schedule, { capture: true, passive: true });
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      sizes?.disconnect();
      window.removeEventListener("resize", schedule);
      document.removeEventListener("scroll", schedule, { capture: true });
    };
  }, [active]);
  return surfaces;
}

export default function FalconAIFab() {
  const { pathname } = useLocation();
  const theme = useTheme();
  const phone = useMediaQuery(theme.breakpoints.down("sm"));
  const skillsRoot = paths.dashboard.skills.root;
  const inSkills = pathname === skillsRoot || pathname.startsWith(`${skillsRoot}/`);
  const surfaces = useSkillsSurfaces(inSkills);
  const navigate = useNavigate();
  const { allowed: falconAllowed } = useFeatureAllowed("falcon_ai");
  const isSidebarOpen = useFalconStore((s) => s.isSidebarOpen);
  const toggleSidebar = useFalconStore((s) => s.toggleSidebar);

  const openFalconAI = useCallback(() => {
    if (!falconAllowed) {
      // Full page shows the capability upsell for unlicensed deployments.
      navigate("/dashboard/falcon-ai");
      return;
    }
    toggleSidebar();
  }, [falconAllowed, navigate, toggleSidebar]);

  // Global keyboard shortcut: Cmd+K (Mac) / Ctrl+K (Windows)
  useEffect(() => {
    const handleKeyDown = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        e.stopPropagation();
        openFalconAI();
      }
    };

    window.addEventListener("keydown", handleKeyDown, true);
    return () => window.removeEventListener("keydown", handleKeyDown, true);
  }, [openFalconAI]);

  // Hide FAB on Falcon AI full-page view
  if (pathname.startsWith("/dashboard/falcon-ai")) return null;

  // Hide FAB when sidebar is open
  if (isSidebarOpen) return null;

  // Skills: the FAB sits 16px from the edges and above any sticky task footer
  // (published height or measured, whichever is larger). On a phone it steps
  // aside while a footer, dialog, drawer or menu is open. Elsewhere it keeps
  // its original placement.
  if (inSkills && phone && (surfaces.footer || surfaces.overlay)) return null;
  const placement = inSkills
    ? {
        bottom: `calc(max(var(--skills-sticky-footer-height, 0px), ${surfaces.footerHeight}px) + 16px)`,
        right: 16,
        zIndex: theme.zIndex.speedDial,
      }
    : { bottom: 72, right: 12, zIndex: 1200 };

  return (
    <CustomTooltip title="Falcon AI (⌘K)" show={true} placement="left" arrow>
      <Fab
        onClick={openFalconAI}
        size="medium"
        sx={{
          position: "fixed",
          ...placement,
          bgcolor: "primary.main",
          color: "primary.contrastText",
          width: 40,
          height: 40,
          minHeight: 40,
          // eslint-disable-next-line no-restricted-syntax -- ds: brand (Falcon button glow)
          boxShadow: "0 3px 12px 0 rgba(120, 87, 252, 0.4)",
          "&:hover": {
            bgcolor: "primary.dark",
            // eslint-disable-next-line no-restricted-syntax -- ds: brand (Falcon button glow)
            boxShadow: "0 4px 18px 0 rgba(120, 87, 252, 0.55)",
            transform: "scale(1.08)",
          },
          transition:
            "box-shadow 0.2s ease, background-color 0.2s ease, transform 0.15s ease, bottom 0.15s ease",
          "@media (prefers-reduced-motion: reduce)": { transition: "none" },
        }}
      >
        <FalconIcon sx={{ fontSize: 20 }} />
      </Fab>
    </CustomTooltip>
  );
}

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

// Skills task footers mark themselves with [data-sticky-footer]; on a phone the
// FAB steps aside while one is mounted instead of covering its actions.
function useStickyFooterMounted(active) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    if (!active || typeof MutationObserver === "undefined") {
      setMounted(false);
      return undefined;
    }
    const check = () => setMounted(Boolean(document.querySelector("[data-sticky-footer]")));
    check();
    const observer = new MutationObserver(check);
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-sticky-footer"] });
    return () => observer.disconnect();
  }, [active]);
  return mounted;
}

export default function FalconAIFab() {
  const { pathname } = useLocation();
  const theme = useTheme();
  const phone = useMediaQuery(theme.breakpoints.down("sm"));
  const skillsRoot = paths.dashboard.skills.root;
  const inSkills = pathname === skillsRoot || pathname.startsWith(`${skillsRoot}/`);
  const footerMounted = useStickyFooterMounted(inSkills && phone);
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

  // Skills: the FAB sits 16px from the edges, under drawers and dialogs, and
  // never over a phone task footer. Elsewhere it keeps its original placement.
  if (inSkills && phone && footerMounted) return null;
  const placement = inSkills
    ? { bottom: 16, right: 16, zIndex: theme.zIndex.speedDial }
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
            "box-shadow 0.2s ease, background-color 0.2s ease, transform 0.15s ease",
        }}
      >
        <FalconIcon sx={{ fontSize: 20 }} />
      </Fab>
    </CustomTooltip>
  );
}

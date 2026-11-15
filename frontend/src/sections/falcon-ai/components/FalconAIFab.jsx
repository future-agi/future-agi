import React, { useCallback, useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import Fab from "@mui/material/Fab";
import SvgIcon from "@mui/material/SvgIcon";
import { paths } from "src/routes/paths";
import CustomTooltip from "src/components/tooltip";
import useFalconStore from "../store/useFalconStore";
import { useFeatureAllowed } from "src/hooks/useCapabilities";

// eslint-disable-next-line react-refresh/only-export-components -- shared with the Skills navigation's assistant button
export function FalconIcon(props) {
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

/**
 * Opens Falcon AI the way the floating button and Cmd+K do: the sidebar when
 * the deployment allows it, otherwise the full page with its upsell. Skills
 * uses it for the assistant button in its navigation row.
 */
// eslint-disable-next-line react-refresh/only-export-components -- the hook belongs with the button it mirrors
export function useOpenFalconAI() {
  const navigate = useNavigate();
  const { allowed: falconAllowed } = useFeatureAllowed("falcon_ai");
  const toggleSidebar = useFalconStore((s) => s.toggleSidebar);
  return useCallback(() => {
    if (!falconAllowed) {
      // Full page shows the capability upsell for unlicensed deployments.
      navigate("/dashboard/falcon-ai");
      return;
    }
    toggleSidebar();
  }, [falconAllowed, navigate, toggleSidebar]);
}

export default function FalconAIFab() {
  const { pathname } = useLocation();
  const skillsRoot = paths.dashboard.skills.root;
  const inSkills = pathname === skillsRoot || pathname.startsWith(`${skillsRoot}/`);
  const isSidebarOpen = useFalconStore((s) => s.isSidebarOpen);
  const openFalconAI = useOpenFalconAI();

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

  // Skills, at every width: no floating button, so nothing ever floats over
  // body text, tables or footers. The same action sits in the Skills
  // navigation row (SkillsWorkspaceLayout); Cmd+K above stays mounted.
  if (inSkills) return null;

  return (
    <CustomTooltip title="Falcon AI (⌘K)" show={true} placement="left" arrow>
      <Fab
        onClick={openFalconAI}
        size="medium"
        sx={{
          position: "fixed",
          bottom: 72,
          right: 12,
          zIndex: 1200,
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

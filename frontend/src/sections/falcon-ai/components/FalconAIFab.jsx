import React, { useEffect } from "react";
import PropTypes from "prop-types";
import Fab from "@mui/material/Fab";
import CustomTooltip from "src/components/tooltip";
import { useFeatureAllowed } from "src/hooks/useCapabilities";
import FalconIcon from "./FalconIcon";
import useFalconLauncher from "../hooks/useFalconLauncher";
import { dsxOwnsCommandShortcut } from "src/sections/common/CommandPalette/shortcutOwner";
import { usePathname } from "src/routes/hooks";
import useFalconStore from "../store/useFalconStore";
import useFabGutter, {
  fabHiddenOnRoute,
  useOverlayOpen,
} from "src/sections/common/ux/useFabGutter";

// Cmd/Ctrl+K belongs to the app's command palette, which offers "Ask
// Falcon…"; the launcher takes the key only where no palette is mounted.
// A denied, pending or failed capability exposes neither button nor shortcut.
export default function FalconAIFab({ docked = false }) {
  const { openFalconAI, hidden } = useFalconLauncher();
  const { allowed: falconAllowed } = useFeatureAllowed("falcon_ai");

  useEffect(() => {
    if (!falconAllowed) return undefined;
    const handleKeyDown = (e) => {
      if (
        (e.metaKey || e.ctrlKey) &&
        !e.altKey &&
        !e.shiftKey &&
        String(e.key).toLowerCase() === "k" &&
        !dsxOwnsCommandShortcut(e)
      ) {
        e.preventDefault();
        e.stopPropagation();
        if (!e.repeat) openFalconAI();
      }
    };

    window.addEventListener("keydown", handleKeyDown, true);
    return () => window.removeEventListener("keydown", handleKeyDown, true);
  }, [falconAllowed, openFalconAI]);

  const pathname = usePathname();
  const isSidebarOpen = useFalconStore((s) => s.isSidebarOpen);
  const overlayOpen = useOverlayOpen();
  const visible =
    falconAllowed &&
    !hidden &&
    !docked &&
    !fabHiddenOnRoute(pathname) &&
    !isSidebarOpen &&
    !overlayOpen;
  useFabGutter(visible);
  if (!visible) return null;

  return (
    <CustomTooltip title="Falcon AI" show={true} placement="left" arrow>
      <Fab
        onClick={openFalconAI}
        aria-label="Open Falcon AI"
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

FalconAIFab.propTypes = {
  docked: PropTypes.bool,
};

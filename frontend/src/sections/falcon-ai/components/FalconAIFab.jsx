import React, { useEffect, useId } from "react";
import PropTypes from "prop-types";
import Fab from "@mui/material/Fab";
import CustomTooltip from "src/components/tooltip";
import { MOD_KEY_LABEL } from "src/utils/keymap";
import FalconIcon from "src/components/falcon-icon";
import useFalconLauncher from "../hooks/useFalconLauncher";
import { registerAssistantLauncher } from "src/sections/common/CommandPalette/shortcutOwner";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";
import { usePathname } from "src/routes/hooks";
import useFalconStore from "../store/useFalconStore";
import useFabGutter, {
  fabHiddenOnRoute,
  useOverlayOpen,
} from "src/sections/common/ux/useFabGutter";

// The Falcon AI button, drawn for everyone, and Cmd/Ctrl+K. Both open the
// assistant where the deployment offers it, and the Falcon AI page with its
// line where it does not (useFalconLauncher). While the capability is still
// loading, or could not be read, the deployment is treated as not offering
// it: the button still shows and opens the page. The launcher takes the key
// on every page it is mounted on, before the command palette
// (shortcutOwner.js); the palette opens from its own control there.
// With the command palette open, the key closes the palette and brings the
// assistant up in its place; a sidebar already open stays open.
const SHORTCUT_HINT = `${MOD_KEY_LABEL}K`;

export default function FalconAIFab({ docked = false }) {
  const { openFalconAI, hidden } = useFalconLauncher();
  const launcherId = useId();
  const isSidebarOpen = useFalconStore((s) => s.isSidebarOpen);

  useEffect(() => registerAssistantLauncher(launcherId), [launcherId]);

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (
        (e.metaKey || e.ctrlKey) &&
        !e.altKey &&
        !e.shiftKey &&
        String(e.key).toLowerCase() === "k"
      ) {
        e.preventDefault();
        e.stopPropagation();
        if (e.repeat) return;
        const shortcuts = useShortcutStore.getState();
        if (shortcuts.paletteOpen) {
          shortcuts.setPaletteOpen(false);
          if (!isSidebarOpen) openFalconAI();
          return;
        }
        openFalconAI();
      }
    };

    window.addEventListener("keydown", handleKeyDown, true);
    return () => window.removeEventListener("keydown", handleKeyDown, true);
  }, [openFalconAI, isSidebarOpen]);

  const pathname = usePathname();
  const overlayOpen = useOverlayOpen();
  const visible =
    !hidden &&
    !docked &&
    !fabHiddenOnRoute(pathname) &&
    !isSidebarOpen &&
    !overlayOpen;
  useFabGutter(visible);
  if (!visible) return null;

  return (
    <CustomTooltip
      title={`Chat (${SHORTCUT_HINT})`}
      show={true}
      placement="left"
      arrow
    >
      <Fab
        onClick={openFalconAI}
        aria-label="Open Falcon AI"
        aria-keyshortcuts="Meta+K Control+K"
        size="medium"
        sx={{
          position: "fixed",
          bottom: 16,
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

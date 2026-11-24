import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Box, Drawer, IconButton, Portal } from "@mui/material";
import Iconify from "src/components/iconify";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

/**
 * The shared right-side drawer, matching the ones in Datasets.
 *
 * The backdrop is transparent, so the page behind stays fully readable — a
 * drawer here is for acting *on* what you were just looking at, and dimming it
 * out is exactly wrong.
 *
 * The background needs a word, because it is not where it looks like it is. The
 * theme's own MuiDrawer override paints every temporary drawer's paper
 * `background.neutral` in dark mode from the drawer *root*
 * (`.root .MuiDrawer-paper`), which outranks anything passed through PaperProps.
 * `&&` lifts specificity above that rule so the paper itself is
 * `background.paper`, keeping the theme's side borders and its drawer shadow.
 *
 * `beside` leaves the page usable while the drawer is open (the builder chat next to Review changes): no backdrop, no
 * focus trap, and only the close button or Escape closes it. It gets a container of its own because a modal hides
 * everything else in its container from screen readers.
 */
export default function SideDrawer({ open, onClose, width = 480, keepMounted = false, beside = false, children }) {
  const [host, setHost] = useState(null);
  const covered = useDrawerCovered(open);
  // Nothing keeps focus in a drawer beside the page: a button that disables itself when pressed drops it to the body,
  // where the drawer's own Escape never fires.
  useEffect(() => {
    if (!beside || !open || covered) return undefined;
    const onKeyDown = (event) => {
      if (event.key !== "Escape" || document.activeElement !== document.body) return;
      onClose?.(event);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [beside, open, covered, onClose]);
  const drawer = (
    <Drawer
      anchor="right"
      // Its own container first: opened before it exists, the drawer would land in the page's.
      open={Boolean(open) && (!beside || Boolean(host))}
      onClose={onClose}
      sx={{
        "&& .MuiDrawer-paper": {
          width,
          maxWidth: "96vw",
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          ...(beside && { pointerEvents: "auto" }),
          borderRadius: "8px",
          backgroundColor: "background.paper",
          backgroundImage: "none",
        },
        ...(beside && { pointerEvents: "none" }),
        ...coveredDrawerSx(covered),
      }}
      ModalProps={{
        keepMounted,
        BackdropProps: { style: { backgroundColor: "transparent" } },
        ...(beside && {
          container: host,
          hideBackdrop: true,
          disableEnforceFocus: true,
          disableScrollLock: true,
        }),
      }}
    >
      {/* The single close affordance for every drawer built on this shell — the
          plain line X. Drawer contents must NOT add their own close button, or
          the two stack up in the same corner. */}
      <IconButton
        aria-label="Close"
        onClick={onClose}
        size="small"
        sx={{ position: "absolute", top: 8, right: 8, zIndex: 1, color: "text.subtitle" }}
      >
        <Iconify icon="solar:close-linear" width={18} />
      </IconButton>
      {children}
    </Drawer>
  );
  if (!beside) return drawer;
  return (
    <>
      <Portal>
        <Box ref={setHost} />
      </Portal>
      {drawer}
    </>
  );
}

SideDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  width: PropTypes.oneOfType([PropTypes.number, PropTypes.string, PropTypes.object]),
  keepMounted: PropTypes.bool,
  beside: PropTypes.bool,
  children: PropTypes.node,
};

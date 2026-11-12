import PropTypes from "prop-types";
import { Drawer, IconButton } from "@mui/material";
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
 */
export default function SideDrawer({ open, onClose, width = 480, keepMounted = false, children }) {
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      sx={{
        "&& .MuiDrawer-paper": {
          width,
          maxWidth: "96vw",
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "8px",
          backgroundColor: "background.paper",
          backgroundImage: "none",
        },
        ...coveredDrawerSx(covered),
      }}
      ModalProps={{
        keepMounted,
        BackdropProps: { style: { backgroundColor: "transparent" } },
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
}

SideDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  width: PropTypes.oneOfType([PropTypes.number, PropTypes.string, PropTypes.object]),
  keepMounted: PropTypes.bool,
  children: PropTypes.node,
};

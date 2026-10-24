import PropTypes from "prop-types";
import { Drawer } from "@mui/material";
import {
  COVERED_DRAWER_SX,
  useDrawerCovered,
} from "src/hooks/use-drawer-stack";

/**
 * The side drawer, matching the ones in Datasets.
 *
 * The backdrop is transparent, so the page behind stays fully readable — a
 * drawer here is for acting *on* what you were just looking at, and dimming it
 * out is exactly wrong.
 *
 * The background needs a word, because it is not where it looks like it is.
 * The theme's own MuiDrawer override paints every temporary drawer's paper
 * `background.neutral` in dark mode, and it does so from the drawer *root*
 * (`.root .MuiDrawer-paper`), which outranks anything passed through
 * PaperProps.sx — so setting a colour there does nothing at all.
 *
 * Datasets' drawers look different because their content lays a full-size Box
 * painted `background.paper` over that paper, so what you actually see is
 * #111111, not the theme's #18181b. Rather than repeat that trick in five
 * content components, the surface is settled once here: `&&` lifts specificity
 * above the theme rule so the paper itself is `background.paper`, and the
 * theme's side borders and the reference drawer's shadow are kept.
 *
 * One drawer on screen at a time: one opened from inside this one hides it
 * until it closes (src/hooks/use-drawer-stack).
 */
export default function SideDrawer({
  open,
  onClose,
  width = 480,
  keepMounted = false,
  persistent = false,
  children,
}) {
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      /* `persistent` keeps the page behind usable while the drawer is open —
         a panel you work alongside (a history), not a dialog. */
      variant={persistent ? "persistent" : "temporary"}
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
        ...(covered && COVERED_DRAWER_SX),
      }}
      ModalProps={{
        keepMounted,
        BackdropProps: { style: { backgroundColor: "transparent" } },
      }}
    >
      {children}
    </Drawer>
  );
}

SideDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  width: PropTypes.oneOfType([
    PropTypes.number,
    PropTypes.string,
    PropTypes.object,
  ]),
  keepMounted: PropTypes.bool,
  persistent: PropTypes.bool,
  children: PropTypes.node,
};

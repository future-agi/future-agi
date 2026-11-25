import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import {
  Box,
  Drawer,
  IconButton,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useDrawerCovered } from "src/hooks/use-drawer-stack";
import { RUN_PLAN_COPY as COPY } from "./readiness/readiness.constants";

// The design system's 640 drawer, never wider than the screen less room for the edge tab.
const PANEL_WIDTH = "min(640px, calc(100vw - 40px))";

/**
 * The run plan's drawer: over the right of the page and not modal, so the builder chat stays usable beside it.
 * Opening, closing and collapsing all slide its width; collapsed, only its edge tab shows.
 */
export default function RunPlanDrawer({
  open,
  subtitle,
  footer,
  busy = false,
  locked = false,
  collapsed = false,
  onCollapsedChange,
  onClose,
  children,
}) {
  const theme = useTheme();
  const still = useMediaQuery("(prefers-reduced-motion: reduce)");
  const slideMs = still ? 0 : theme.transitions.duration.standard;
  const [entered, setEntered] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const closing = useRef(null);
  const bodyRef = useRef(null);
  // Another drawer opened over the plan folds it to its tab, so both stay reachable.
  const covered = useDrawerCovered(open);
  const [shownOver, setShownOver] = useState(false);
  useEffect(() => {
    if (!covered) setShownOver(false);
  }, [covered]);
  // Keyboard focus starts in the plan, and goes back where it came from.
  useEffect(() => {
    const before = document.activeElement;
    const body = bodyRef.current;
    const frame = requestAnimationFrame(() => {
      setEntered(true);
      body?.focus({ preventScroll: true });
    });
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(closing.current);
      if (body?.contains(document.activeElement)) before?.focus?.();
    };
  }, []);
  const close = () => {
    setLeaving(true);
    closing.current = setTimeout(onClose, slideMs);
  };
  const folded = collapsed || (covered && !shownOver);
  const toggle = () => {
    setShownOver(folded && covered);
    onCollapsedChange?.(!folded);
  };
  // Escape closes it from inside, or when a button that disabled itself left focus nowhere; never from the chat.
  const closeRef = useRef(null);
  closeRef.current = folded || locked || leaving ? null : close;
  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const at = document.activeElement;
      if (at === document.body || bodyRef.current?.contains(at))
        closeRef.current?.();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  const toggleLabel = folded ? COPY.expand : COPY.collapse;
  // Starts at zero width and slides open like expanding; closing slides shut like collapsing, then unmounts.
  const narrow = folded || !entered || leaving;

  return (
    <Drawer
      data-scenario-run-dialog=""
      open={open}
      anchor="right"
      variant="persistent"
      hideBackdrop
      PaperProps={{
        component: "aside",
        "aria-label": COPY.title,
        sx: {
          height: "100vh",
          position: "fixed",
          // Another drawer slides over the same edge: the tab, and the plan shown again, sit above it.
          zIndex: covered ? theme.zIndex.drawer + 1 : 110,
          boxShadow: "none",
          borderRadius: 0,
          borderLeft: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
          display: "flex",
          overflow: "visible",
          minWidth: narrow ? 0 : PANEL_WIDTH,
          maxWidth: narrow ? 0 : PANEL_WIDTH,
          transition: theme.transitions.create(["min-width", "max-width"], {
            duration: slideMs,
          }),
        },
      }}
    >
      {/* The tab on the drawer's left edge; the only part left when collapsed. */}
      <CustomTooltip
        show
        size="small"
        arrow
        placement="left"
        title={toggleLabel}
      >
        <IconButton
          size="small"
          aria-label={toggleLabel}
          aria-expanded={!folded}
          onClick={toggle}
          // right: 100% puts its right edge on the paper's padding edge, so it covers the paper's 1px left border and
          // the two read as one.
          sx={{
            position: "absolute",
            top: "48px",
            right: "100%",
            zIndex: 1,
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            borderRight: 0,
            borderTopRightRadius: 0,
            borderBottomRightRadius: 0,
            "&:hover": { bgcolor: "background.default" },
          }}
        >
          <Iconify
            icon="solar:alt-arrow-right-linear"
            width={16}
            sx={{
              color: "text.tertiary",
              transform: folded ? "rotate(180deg)" : "rotate(0deg)",
              transition: theme.transitions.create("transform", {
                duration: still ? 0 : theme.transitions.duration.shorter,
              }),
            }}
          />
          {busy && folded && (
            <Box
              data-testid="run-plan-busy"
              sx={{
                position: "absolute",
                top: 2,
                right: 2,
                width: 7,
                height: 7,
                borderRadius: "50%",
                bgcolor: "status.info.solid",
                border: "1.5px solid",
                borderColor: "background.paper",
              }}
            />
          )}
        </IconButton>
      </CustomTooltip>
      {/* Hidden, not unmounted, while collapsed: picks, queries and a fix's polling carry on, and nothing hidden takes
          focus. Its width stays fixed while the drawer slides, so it is revealed, not reflowed. */}
      <Stack
        ref={bodyRef}
        tabIndex={-1}
        sx={{
          // The paper's width less its 1px left border.
          width: `calc(${PANEL_WIDTH} - 1px)`,
          flexShrink: 0,
          height: "100%",
          overflow: "hidden",
          outline: "none",
          visibility: folded || leaving ? "hidden" : "visible",
        }}
      >
        <Stack
          direction="row"
          spacing={0.5}
          alignItems="flex-start"
          sx={{ px: 2, pt: 1.5, pb: 1 }}
        >
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography
              component="h2"
              sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}
            >
              {COPY.title}
            </Typography>
            {subtitle && (
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {subtitle}
              </Typography>
            )}
          </Box>
          <CustomTooltip show={!locked} size="small" arrow title={COPY.close}>
            <IconButton
              aria-label={COPY.close}
              onClick={close}
              disabled={locked}
              size="small"
              sx={{
                color: "text.subtitle",
                "&:hover": { color: "text.primary" },
              }}
            >
              <Iconify icon="solar:close-linear" width={16} />
            </IconButton>
          </CustomTooltip>
        </Stack>
        <Stack
          spacing={2}
          sx={{ flex: 1, minHeight: 0, overflowY: "auto", px: 2, py: 1 }}
        >
          {children(close)}
        </Stack>
        <Stack
          direction="row"
          spacing={1}
          justifyContent="flex-end"
          sx={{
            px: 2,
            py: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {footer(close)}
        </Stack>
      </Stack>
    </Drawer>
  );
}

RunPlanDrawer.propTypes = {
  open: PropTypes.bool,
  subtitle: PropTypes.string,
  // (close) -> the buttons, and the body; close slides it shut first.
  footer: PropTypes.func.isRequired,
  // Work goes on while it is collapsed, e.g. a fix.
  busy: PropTypes.bool,
  // Can't be closed, e.g. while a run is being started.
  locked: PropTypes.bool,
  collapsed: PropTypes.bool,
  onCollapsedChange: PropTypes.func,
  onClose: PropTypes.func,
  children: PropTypes.func.isRequired,
};

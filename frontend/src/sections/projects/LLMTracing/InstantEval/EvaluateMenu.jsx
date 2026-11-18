import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Menu, MenuItem, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import EnterpriseMark from "src/components/capability-gate/EnterpriseMark";
import Iconify from "src/components/iconify";
import { CAPABILITY, useFeatureDenied } from "src/hooks/useCapabilities";
import { unitMeta } from "./instantEvalUnits";
import { pillSx } from "../toolbarStyles";

// Marks an action whose feature this deployment doesn't include. Rendered
// inside the open menu only, so the toolbar itself needs no capability read.
function ActionLock({ feature, sx }) {
  return useFeatureDenied(feature) ? <EnterpriseMark sx={sx} /> : null;
}

ActionLock.propTypes = {
  feature: PropTypes.string.isRequired,
  sx: PropTypes.object,
};

/**
 * The toolbar's one entry point for judging the rows in view: ask Jev a
 * question about each of them (Instant eval), start an Insights report on
 * them, or run saved evaluators. Each action shows only when its handler is
 * given; with none, nothing renders. Instant eval and Insights are
 * Enterprise: without them an action is marked and still opens, onto the
 * upgrade gate its page shows.
 */
export default function EvaluateMenu({
  unit,
  onAsk,
  onFindPatterns,
  onAddEvaluators,
}) {
  const [anchor, setAnchor] = useState(null);
  const menuId = useId();
  const meta = unitMeta(unit);
  const actions = [
    onAsk && {
      key: "ask",
      icon: "mdi:creation-outline",
      title: "Ask a question",
      detail: `Jev answers it for a sample or every ${meta.noun} in view; the first answers come in seconds.`,
      feature: CAPABILITY.INSTANT_EVAL,
      onClick: onAsk,
    },
    onFindPatterns && {
      key: "patterns",
      icon: "mdi:chart-box-outline",
      title: "Find patterns",
      detail: `Start an Insights report on these ${meta.plural}.`,
      feature: CAPABILITY.INSIGHTS,
      onClick: onFindPatterns,
    },
    onAddEvaluators && {
      key: "evaluators",
      icon: "mdi:playlist-check",
      title: "Add evaluators",
      detail: "Run saved evaluators on this view, once or continuously.",
      onClick: onAddEvaluators,
    },
  ].filter(Boolean);
  if (!actions.length) return null;
  const open = Boolean(anchor);

  return (
    <>
      <Button
        variant="outlined"
        size="small"
        id={`${menuId}-button`}
        aria-haspopup="menu"
        aria-expanded={open ? "true" : undefined}
        aria-controls={open ? menuId : undefined}
        startIcon={<Iconify icon="mdi:creation-outline" width={16} />}
        endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
        onClick={(event) => setAnchor(event.currentTarget)}
        sx={{
          ...pillSx,
          ...(open && { bgcolor: "action.hover" }),
          "& .MuiButton-endIcon": { ml: 0.5 },
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: 1,
          },
        }}
      >
        Evaluate
      </Button>
      <Menu
        id={menuId}
        anchorEl={anchor}
        open={open}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        MenuListProps={{ "aria-labelledby": `${menuId}-button`, dense: true }}
        slotProps={{
          paper: { sx: { mt: 0.5, borderRadius: "8px", width: 400 } },
        }}
      >
        {actions.map((action) => (
          <MenuItem
            key={action.key}
            onClick={() => {
              setAnchor(null);
              action.onClick();
            }}
            sx={{
              alignItems: "flex-start",
              gap: 1.25,
              py: 1,
              px: 1.5,
              whiteSpace: "normal",
            }}
          >
            <Box
              sx={{
                width: 28,
                height: 28,
                borderRadius: "6px",
                flexShrink: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "primary.main",
                bgcolor: (theme) => alpha(theme.palette.primary.main, 0.08),
              }}
            >
              <Iconify icon={action.icon} width={16} />
            </Box>
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="body2" fontWeight={600}>
                {action.title}
                {action.feature && (
                  <ActionLock feature={action.feature} sx={{ ml: 0.75 }} />
                )}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                component="p"
                sx={{ lineHeight: 1.4 }}
              >
                {action.detail}
              </Typography>
            </Box>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}

EvaluateMenu.propTypes = {
  unit: PropTypes.oneOf(["traces", "spans", "sessions", "users"]).isRequired,
  onAsk: PropTypes.func,
  onFindPatterns: PropTypes.func,
  onAddEvaluators: PropTypes.func,
};

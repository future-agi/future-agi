import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  ListItemIcon,
  ListItemText,
  Popover,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";

import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { UI_ONLY_STARTED, plural } from "./runHeaderFormat";

const buttonSx = {
  color: "text.primary",
  borderColor: "divider",
  typography: "s2",
  fontWeight: 600,
  flexShrink: 0,
};
const itemSx = {
  alignItems: "flex-start",
  gap: 0.75,
  py: 1.25,
  whiteSpace: "normal",
};
const itemText = {
  primaryTypographyProps: { typography: "s2", fontWeight: 600 },
  secondaryTypographyProps: {
    typography: "s3",
    color: "text.subtitle",
    sx: { whiteSpace: "normal" },
  },
};

/**
 * Re-run from the run header. After a run with failures the one-click action
 * re-runs the scenarios that didn't pass; the caret always re-runs the whole
 * run, in one of the two modes. "Re-run exact snapshot" has no backend yet and
 * starts nothing.
 */
export default function RerunMenu({
  failing = false,
  scenarios,
  runLabel,
  trials = 1,
  disabledReason = null,
  onRun,
  onRunWhole,
}) {
  const [anchor, setAnchor] = useState(null);
  const disabled = !!disabledReason;
  const heading = `Re-run ${runLabel} · ${plural(scenarios, "scenario")}`;
  const snapshot = () => {
    setAnchor(null);
    enqueueSnackbar(UI_ONLY_STARTED, { variant: "info" });
  };
  const runFailing = () => onRun();
  const runNow = () => {
    setAnchor(null);
    (onRunWhole ?? onRun)();
  };
  const caret = (e) => setAnchor(e.currentTarget);

  return (
    <>
      <CustomTooltip
        show={disabled}
        arrow
        size="small"
        title={disabledReason || ""}
      >
        {/* A disabled button fires no hover events; the box carries the tip. */}
        <Box sx={{ display: "flex", flexShrink: 0 }}>
          {failing ? (
            <Stack direction="row">
              <Button
                variant="outlined"
                size="small"
                disabled={disabled}
                onClick={runFailing}
                startIcon={<Iconify icon="solar:refresh-linear" width={15} />}
                sx={{
                  ...buttonSx,
                  borderTopRightRadius: 0,
                  borderBottomRightRadius: 0,
                }}
              >
                Re-run what didn&apos;t pass
              </Button>
              <Button
                variant="outlined"
                size="small"
                aria-label="More re-run options"
                disabled={disabled}
                onClick={caret}
                sx={{
                  ...buttonSx,
                  minWidth: 0,
                  px: 0.75,
                  borderLeft: "none",
                  borderTopLeftRadius: 0,
                  borderBottomLeftRadius: 0,
                }}
              >
                <Iconify
                  icon="solar:alt-arrow-down-linear"
                  width={12}
                  sx={{ color: "text.subtitle" }}
                />
              </Button>
            </Stack>
          ) : (
            <Button
              variant="outlined"
              size="small"
              disabled={disabled}
              onClick={caret}
              startIcon={<Iconify icon="solar:refresh-linear" width={15} />}
              endIcon={
                <Iconify
                  icon="solar:alt-arrow-down-linear"
                  width={12}
                  sx={{ color: "text.subtitle" }}
                />
              }
              sx={buttonSx}
            >
              Re-run
            </Button>
          )}
        </Box>
      </CustomTooltip>
      <Popover
        open={!!anchor}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: { sx: { mt: 0.75, width: 380, borderRadius: "8px" } },
        }}
      >
        <Box sx={{ px: 2, pt: 1.75, pb: 1 }}>
          <Typography
            sx={{ typography: "s3", fontWeight: 600, color: "text.subtitle" }}
          >
            {heading}
          </Typography>
        </Box>
        <Box role="menu">
          <MenuItem onClick={snapshot} sx={itemSx}>
            <ListItemIcon sx={{ minWidth: "0 !important", mt: "3px" }}>
              <Iconify
                icon="solar:history-linear"
                width={16}
                sx={{ color: "text.primary" }}
              />
            </ListItemIcon>
            <ListItemText
              primary={
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <span>
                    Re-run exact snapshot
                    <Box component="span" sx={{ ml: 0.75, typography: "s3" }}>
                      default for comparisons
                    </Box>
                  </span>
                  <UiOnlyFlag feature="rerunSnapshot" />
                </Stack>
              }
              secondary={`Same scenario versions, environment version, pins and seed, ${plural(trials, "repeat")}. Only the agent version can change.`}
              {...itemText}
            />
          </MenuItem>
          <MenuItem onClick={runNow} sx={itemSx}>
            <ListItemIcon sx={{ minWidth: "0 !important", mt: "3px" }}>
              <Iconify
                icon="solar:refresh-linear"
                width={16}
                sx={{ color: "text.primary" }}
              />
            </ListItemIcon>
            <ListItemText
              primary="Run as configured now"
              secondary={`Picks up every edit since: today's agent, scenarios and evals, ${plural(trials, "repeat")}, in a new run.`}
              {...itemText}
            />
          </MenuItem>
        </Box>
      </Popover>
    </>
  );
}
RerunMenu.propTypes = {
  // The run has failing scenarios it can name, so re-run those.
  failing: PropTypes.bool,
  // The whole run's scenarios, named when it re-runs whole.
  scenarios: PropTypes.number.isRequired,
  runLabel: PropTypes.string.isRequired,
  trials: PropTypes.number,
  disabledReason: PropTypes.string,
  // The one-click re-run: the failing scenarios, or the whole run.
  onRun: PropTypes.func.isRequired,
  // The caret's "Run as configured now": the whole run. Defaults to onRun.
  onRunWhole: PropTypes.func,
};

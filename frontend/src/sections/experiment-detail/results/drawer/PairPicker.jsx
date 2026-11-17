import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  IconButton,
  ListSubheader,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { copyToClipboard } from "src/utils/utils";
import { runLabel, runShortName } from "src/sections/common/Runs/runLabel";

/** `vs=` → `{runId, trial?}` · `{none: true}` · null. */
export function parseVs(value) {
  if (!value) return null;
  if (value === "none") return { none: true };
  const at = value.lastIndexOf("@");
  if (at > 0) {
    const t = Number.parseInt(value.slice(at + 1), 10);
    return {
      runId: value.slice(0, at),
      trial: Number.isFinite(t) && t > 0 ? t : null,
    };
  }
  return { runId: value, trial: null };
}

/** The inverse of parseVs (`none`, `<id>`, `<id>@2`). */
export function formatVs(v) {
  if (!v || v.none || !v.runId) return "none";
  return v.trial && v.trial > 1 ? `${v.runId}@${v.trial}` : v.runId;
}

const short = (run) => {
  const name = runShortName(run);
  return name.length > 34 ? `${name.slice(0, 33)}…` : name;
};

/**
 * One side of the trace pair: `#2B Support Agent v3 · trial 1 ▾`. The menu
 * lists every run (and each trial) with a copy-trace-id button per trace.
 */
const PairPicker = ({
  runs,
  value,
  trialsOf,
  traceOf,
  onChange,
  allowNone,
  ariaLabel,
}) => {
  const [anchor, setAnchor] = useState(null);
  const current = value?.runId
    ? runs.find((r) => r.run_id === value.runId)
    : null;
  const text = current
    ? `${runLabel(current)} ${short(current)} · trial ${value.trial ?? 1}`
    : "One trace only";
  return (
    <>
      <Button
        variant="outlined"
        size="small"
        aria-label={ariaLabel}
        onClick={(e) => setAnchor(e.currentTarget)}
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        sx={{
          height: 28,
          px: 1,
          typography: "s2",
          color: "text.primary",
          borderColor: "divider",
          maxWidth: 340,
        }}
      >
        <Box
          component="span"
          sx={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {text}
        </Box>
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        slotProps={{ paper: { sx: { minWidth: 320, maxHeight: 420 } } }}
      >
        {allowNone && (
          <MenuItem
            selected={!current}
            onClick={() => {
              setAnchor(null);
              onChange(null, null);
            }}
            sx={{ typography: "s2" }}
          >
            One trace only
          </MenuItem>
        )}
        {allowNone && <Divider />}
        {runs.flatMap((run) => {
          const n = Math.max(1, trialsOf(run.run_id));
          return [
            <ListSubheader
              key={`h-${run.run_id}`}
              sx={{
                typography: "s3",
                lineHeight: "28px",
                color: "text.secondary",
              }}
            >
              {`${runLabel(run)} ${runShortName(run)}`}
            </ListSubheader>,
            ...Array.from({ length: n }, (_, i) => {
              const t = i + 1;
              const traceId = traceOf(run.run_id, t);
              const selected =
                current?.run_id === run.run_id && (value?.trial ?? 1) === t;
              return (
                <MenuItem
                  key={`${run.run_id}-${t}`}
                  selected={selected}
                  onClick={() => {
                    setAnchor(null);
                    onChange(run.run_id, t);
                  }}
                  sx={{ typography: "s2", pl: 3, gap: 1 }}
                >
                  <Typography typography="s2" sx={{ flex: 1 }}>
                    {`${runLabel(run)} · trial ${t}`}
                  </Typography>
                  {traceId ? (
                    <CustomTooltip
                      show
                      size="small"
                      placement="left"
                      arrow
                      title={`Copy trace id ${traceId}`}
                    >
                      <IconButton
                        size="small"
                        aria-label="Copy trace id"
                        onClick={(e) => {
                          e.stopPropagation();
                          copyToClipboard(traceId);
                          enqueueSnackbar("Trace id copied", {
                            variant: "success",
                          });
                        }}
                        sx={{ p: 0.25, color: "text.secondary" }}
                      >
                        <Iconify icon="basil:copy-outline" width={14} />
                      </IconButton>
                    </CustomTooltip>
                  ) : (
                    <Typography typography="s3" color="text.disabled">
                      No trace
                    </Typography>
                  )}
                </MenuItem>
              );
            }),
          ];
        })}
      </Menu>
    </>
  );
};

PairPicker.propTypes = {
  runs: PropTypes.array.isRequired,
  value: PropTypes.shape({ runId: PropTypes.string, trial: PropTypes.number }),
  trialsOf: PropTypes.func.isRequired,
  traceOf: PropTypes.func.isRequired,
  onChange: PropTypes.func.isRequired,
  allowNone: PropTypes.bool,
  ariaLabel: PropTypes.string,
};

export default PairPicker;

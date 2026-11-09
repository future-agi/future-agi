import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Popover, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { runLineText } from "./RunLine";

/**
 * IT-R5 G3 (P1): the run chips never take a row of their own, at any
 * width. They sit behind ONE chip, `▮▮▮▮▮ 5 runs · baseline #2B ▾`, whose
 * popover holds the full run bar (each run's menu, `Baseline:` picker,
 * `Add runs`).
 */
export default function RunsChip({
  short = false,
  runs,
  colors,
  baselineId,
  children,
  open: openProp,
  onOpenChange,
}) {
  const ref = useRef(null);
  const [openState, setOpenState] = useState(false);
  const open = openProp ?? openState;
  const setOpen = (v) => (onOpenChange ? onOpenChange(v) : setOpenState(v));
  const live = (runs ?? []).filter((r) => !r.not_found);
  const text = runLineText(runs, baselineId);
  return (
    <>
      <ButtonBase
        ref={ref}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`${text}. Show the runs and change the baseline`}
        title={live.map((r) => `${r.label} ${r.name ?? ""}`.trim()).join(" · ")}
        onClick={() => setOpen(true)}
        data-testid="run-line"
        sx={{
          flexShrink: 0,
          height: 24,
          px: 0.75,
          gap: 0.75,
          borderRadius: 0.5,
          border: "1px solid",
          borderColor: "divider",
          typography: "s2",
          color: "text.secondary",
          whiteSpace: "nowrap",
          "&:hover": { bgcolor: "action.hover" },
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
          },
        }}
      >
        <Box component="span" sx={{ display: "inline-flex", gap: 0.25 }}>
          {live.map((r) => (
            <Box
              key={r.run_id}
              component="span"
              sx={{
                width: 6,
                height: 12,
                borderRadius: 0.25,
                bgcolor: colors?.[r.run_id]?.solid ?? "text.disabled",
              }}
            />
          ))}
        </Box>
        <span>{short ? text.split(" · ")[0] : text}</span>
        <Iconify icon="mdi:chevron-down" width={14} />
      </ButtonBase>
      <Popover
        open={open}
        anchorEl={ref.current}
        onClose={() => setOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: { mt: 0.5, p: 1.5, width: 720, maxWidth: "calc(100vw - 32px)" },
            "data-testid": "run-bar-popover",
            role: "dialog",
            "aria-label": "Runs in this view",
          },
        }}
      >
        <Typography
          typography="s1"
          fontWeight="fontWeightSemiBold"
          sx={{ mb: 1 }}
        >
          Runs in this view
        </Typography>
        {children}
      </Popover>
    </>
  );
}

RunsChip.propTypes = {
  /** Below 1500 px: `5 runs` (the baseline is in the label and popover). */
  short: PropTypes.bool,
  runs: PropTypes.array,
  colors: PropTypes.object,
  baselineId: PropTypes.string,
  children: PropTypes.node,
  /** Controlled open (`Change baseline` from an empty state opens it). */
  open: PropTypes.bool,
  onOpenChange: PropTypes.func,
};

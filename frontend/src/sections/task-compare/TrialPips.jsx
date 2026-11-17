import React from "react";
import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { pipTooltip, trialTone } from "./workspaceModel";

const PIP = {
  pass: { icon: "mdi:check", color: "success.main" },
  fail: { icon: "mdi:close", color: "error.main" },
  error: { icon: "mdi:exclamation", color: "error.main" },
  infra: { icon: "mdi:minus", color: "text.disabled" },
  carried: { icon: "mdi:arrow-right-thin", color: "text.secondary" },
  production: { icon: "mdi:access-point", color: "text.secondary" },
  none: { icon: "mdi:minus", color: "text.disabled" },
};

/**
 * Trial pips of a lane: one per trial (icon + colour, never colour alone);
 * the selected trial wears the run-colour ring. Click or `[` `]` (focused
 * lane). Tooltip `Trial 2 · failed · order_found ✕ · $0.010 · 9.1 s`.
 */
export default function TrialPips({
  trials,
  selected,
  onSelect,
  ringColor,
  keyHint,
}) {
  if (!trials?.length) return null;
  // IT-R4 G4 P5: one control, the ARIA way to pick one of N (a radio group:
  // one tab stop, arrows move between trials), not N buttons.
  const pickBy = (step) => {
    const i = trials.findIndex((t) => t.trial === selected);
    const next = trials[(i + step + trials.length) % trials.length];
    if (next && onSelect) onSelect(next.trial);
  };
  return (
    <Stack
      direction="row"
      spacing={0.5}
      alignItems="center"
      role="radiogroup"
      aria-label="Trials"
      onKeyDown={(e) => {
        if (e.key === "ArrowRight" || e.key === "ArrowDown") {
          e.preventDefault();
          pickBy(1);
        } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
          e.preventDefault();
          pickBy(-1);
        }
      }}
    >
      {trials.map((t) => {
        const tone = trialTone(t);
        const p = PIP[tone] ?? PIP.none;
        const on = t.trial === selected;
        return (
          <CustomTooltip
            describeChild
            key={t.trial}
            show
            size="small"
            title={`${pipTooltip(t)}${keyHint ? ` · ${keyHint}` : ""}`}
          >
            <Box
              // The shown trial is the checked radio; a click shows another.
              component="span"
              role="radio"
              aria-checked={on}
              tabIndex={on ? 0 : -1}
              aria-label={`${on ? "Shown: " : "Show "}${pipTooltip(t)}`}
              aria-current={on ? "true" : undefined}
              onClick={onSelect && !on ? () => onSelect(t.trial) : undefined}
              sx={{
                all: "unset",
                boxSizing: "border-box",
                width: 18,
                height: 18,
                borderRadius: "50%",
                display: "grid",
                placeItems: "center",
                cursor: onSelect && !on ? "pointer" : "default",
                border: "1.5px solid",
                borderColor: on ? ringColor ?? "primary.main" : "divider",
                boxShadow: on
                  ? (th) =>
                      `0 0 0 1.5px ${th.palette.background.paper}, 0 0 0 3px ${ringColor ?? th.palette.primary.main}`
                  : "none",
                bgcolor: "background.paper",
                "&:focus-visible": {
                  outline: "2px solid",
                  outlineColor: "primary.main",
                  outlineOffset: 2,
                },
              }}
            >
              <Iconify icon={p.icon} width={12} sx={{ color: p.color }} />
            </Box>
          </CustomTooltip>
        );
      })}
    </Stack>
  );
}

TrialPips.propTypes = {
  trials: PropTypes.array,
  selected: PropTypes.number,
  onSelect: PropTypes.func,
  ringColor: PropTypes.string,
  keyHint: PropTypes.string,
};

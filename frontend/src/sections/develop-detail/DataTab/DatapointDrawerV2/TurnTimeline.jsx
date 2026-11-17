import React, { useRef } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { turnStrip } from "src/sections/develop-detail/TaskSuite/itemModel";

/**
 * F9 turn strip above the bubbles: `1 Customer · 2 Agent ✓ · 3 Customer ·
 * 4 Agent ✗ · …` (✓ / ✗ = the agent turn's Helpfulness; the first failing
 * agent turn is red). Click a turn to scroll to it; ←/→ move between turns.
 * Hidden under 3 turns. One line; wraps to two for long sessions.
 */
const TurnTimeline = ({ conv, current, onPick }) => {
  const refs = useRef({});
  const items = turnStrip(conv);
  if (items.length < 3) return null;
  const move = (n, step) => {
    const i = items.findIndex((t) => t.n === n);
    const next = items[Math.max(0, Math.min(items.length - 1, i + step))];
    if (!next) return;
    refs.current[next.n]?.focus();
    onPick(next.n);
  };
  return (
    <Box
      role="toolbar"
      aria-label="Turns"
      data-testid="turn-strip"
      sx={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        rowGap: 0.25,
        columnGap: 0.25,
        maxHeight: 52,
        overflow: "hidden",
      }}
    >
      {items.map((t, i) => {
        const active = t.n === current;
        const tone =
          t.mark === "✗" ? "red.600" : t.mark === "✓" ? "green.600" : null;
        return (
          <React.Fragment key={t.n}>
            {i > 0 && (
              <Typography typography="s3" color="text.disabled" aria-hidden>
                ·
              </Typography>
            )}
            <CustomTooltip
              show={t.failing}
              size="small"
              placement="top"
              arrow
              title="First failing turn"
            >
              <ButtonBase
                ref={(el) => {
                  refs.current[t.n] = el;
                }}
                data-testid={`turn-strip-${t.n}`}
                tabIndex={active || (!current && i === 0) ? 0 : -1}
                aria-current={active ? "step" : undefined}
                onClick={() => onPick(t.n)}
                onKeyDown={(e) => {
                  if (e.key === "ArrowRight") {
                    e.preventDefault();
                    e.stopPropagation();
                    move(t.n, 1);
                  } else if (e.key === "ArrowLeft") {
                    e.preventDefault();
                    e.stopPropagation();
                    move(t.n, -1);
                  }
                }}
                sx={{
                  px: 0.625,
                  height: 22,
                  borderRadius: 0.5,
                  typography: "s3",
                  whiteSpace: "nowrap",
                  color: t.failing ? "red.600" : "text.secondary",
                  fontWeight: t.failing || active ? 600 : 400,
                  border: "1px solid",
                  borderColor: active ? "primary.main" : "transparent",
                  bgcolor: active ? "action.selected" : "transparent",
                  "&:hover": { bgcolor: "action.hover" },
                  "&.Mui-focusVisible": {
                    outline: "2px solid",
                    outlineColor: "primary.main",
                  },
                }}
              >
                {t.label}
                {t.mark && (
                  <Box
                    component="span"
                    sx={{ ml: 0.375, color: tone, fontWeight: 700 }}
                  >
                    {t.mark}
                  </Box>
                )}
              </ButtonBase>
            </CustomTooltip>
          </React.Fragment>
        );
      })}
    </Box>
  );
};

TurnTimeline.propTypes = {
  conv: PropTypes.object.isRequired,
  current: PropTypes.number,
  onPick: PropTypes.func.isRequired,
};

export default TurnTimeline;

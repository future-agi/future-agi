import { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Stack, Tooltip, Typography } from "@mui/material";
import { TRIAL_PICKER_COPY as C } from "./callTraces.constants";
import { runOutcome } from "./trace/traceTable.constants";

export default function TrialPicker({ trials, currentId, onPick }) {
  const listRef = useRef(null);
  // The open trial stays in view when the list scrolls; only the list moves.
  useEffect(() => {
    const list = listRef.current;
    const on = list?.querySelector('[aria-pressed="true"]');
    if (!on) return;
    if (on.offsetLeft < list.scrollLeft) list.scrollLeft = on.offsetLeft;
    else if (
      on.offsetLeft + on.offsetWidth >
      list.scrollLeft + list.clientWidth
    )
      list.scrollLeft = on.offsetLeft + on.offsetWidth - list.clientWidth;
  }, [currentId, trials]);
  if (!trials || trials.length < 2) return null;
  return (
    // Many trials scroll sideways, so the header keeps room for the call's id.
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{ minWidth: 0, maxWidth: "50%" }}
    >
      <Typography sx={{ typography: "s2", fontWeight: 600, flexShrink: 0 }}>
        {C.label}
      </Typography>
      <Stack
        ref={listRef}
        direction="row"
        spacing={0.5}
        sx={{
          position: "relative",
          minWidth: 0,
          overflowX: "auto",
          scrollbarWidth: "thin",
        }}
      >
        {trials.map((trial) => {
          // Named and coloured as the run's calls table names a call's outcome.
          const outcome = runOutcome(trial.status);
          const on = trial.id === currentId;
          return (
            <Tooltip
              key={trial.id}
              arrow
              title={C.tooltip(trial.trialIndex, outcome.label)}
            >
              <ButtonBase
                aria-pressed={on}
                aria-label={C.tooltip(trial.trialIndex, outcome.label)}
                onClick={() => onPick(trial.id)}
                sx={{
                  flexShrink: 0,
                  height: 22,
                  px: 0.75,
                  gap: 0.5,
                  borderRadius: "6px",
                  border: "1px solid",
                  borderColor: on ? "text.primary" : "divider",
                  typography: "s3",
                  fontWeight: on ? 600 : 400,
                }}
              >
                <Box
                  component="span"
                  sx={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    bgcolor: outcome.color,
                  }}
                />
                {trial.trialIndex}
              </ButtonBase>
            </Tooltip>
          );
        })}
      </Stack>
    </Stack>
  );
}

TrialPicker.propTypes = {
  trials: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      trialIndex: PropTypes.number.isRequired,
      status: PropTypes.string,
    }),
  ),
  currentId: PropTypes.string,
  onPick: PropTypes.func.isRequired,
};

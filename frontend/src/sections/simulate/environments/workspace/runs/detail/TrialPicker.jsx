import PropTypes from "prop-types";
import { Box, ButtonBase, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { STATUS_META } from "../runs.constants";
import { TRIAL_PICKER_COPY as C } from "./callTraces.constants";

const ROLE_COLOR = {
  success: "success.main",
  danger: "error.main",
  warning: "warning.main",
  info: "info.main",
  neutral: "text.disabled",
};

export default function TrialPicker({ trials, currentId, onPick }) {
  if (!trials || trials.length < 2) return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{ flexShrink: 0 }}
    >
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {C.label}
      </Typography>
      {trials.map((trial) => {
        const meta = STATUS_META[trial.status] || STATUS_META.unmeasured;
        const on = trial.id === currentId;
        return (
          <CustomTooltip
            key={trial.id}
            show
            size="small"
            arrow
            title={C.tooltip(trial.trialIndex, meta.label)}
          >
            <ButtonBase
              aria-pressed={on}
              aria-label={C.tooltip(trial.trialIndex, meta.label)}
              onClick={() => onPick(trial.id)}
              sx={{
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
                  bgcolor: ROLE_COLOR[meta.role] || ROLE_COLOR.neutral,
                }}
              />
              {trial.trialIndex}
            </ButtonBase>
          </CustomTooltip>
        );
      })}
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

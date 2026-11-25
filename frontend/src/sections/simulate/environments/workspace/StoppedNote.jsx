import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { STOPPED_NOTE_COPY } from "./workspace.constants";

export default function StoppedNote({ wait, sx }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      data-testid="stopped-note"
      sx={{ px: 1.5, py: 1.25, ...sx }}
    >
      <Iconify
        icon="solar:stop-circle-linear"
        width={14}
        sx={{ color: "text.tertiary", flexShrink: 0 }}
      />
      <Typography
        sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
      >
        {STOPPED_NOTE_COPY.text(wait.step)}
      </Typography>
      {wait.reason && (
        <CustomTooltip
          show
          arrow
          size="small"
          title={STOPPED_NOTE_COPY.tip(wait.reason)}
        >
          <Box
            component="span"
            tabIndex={0}
            aria-label={STOPPED_NOTE_COPY.why}
            sx={{ display: "inline-flex", flexShrink: 0, cursor: "help" }}
          >
            <Iconify
              icon="solar:info-circle-linear"
              width={14}
              sx={{ color: "text.disabled" }}
            />
          </Box>
        </CustomTooltip>
      )}
    </Stack>
  );
}

StoppedNote.propTypes = {
  wait: PropTypes.shape({
    step: PropTypes.string,
    reason: PropTypes.string,
  }).isRequired,
  sx: PropTypes.object,
};

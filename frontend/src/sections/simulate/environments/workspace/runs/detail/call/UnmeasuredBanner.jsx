import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

import SoonChip from "../../../../components/SoonChip";
import { UNMEASURED_LABEL, isUnmeasured } from "./callDrawer.utils";

/**
 * Vel's banner for a call that produced no verdict, read from the real
 * outcome. Vel names the cause (simulator, grader, infrastructure); the API
 * doesn't say yet.
 * @param {{ status: string, harnessOutcomeStatus: ?string, sx: ?Object }} props
 */
export default function UnmeasuredBanner({ status, harnessOutcomeStatus, sx }) {
  if (!isUnmeasured(status)) return null;
  const label = UNMEASURED_LABEL[status];
  const harness =
    harnessOutcomeStatus &&
    harnessOutcomeStatus.toLowerCase() !== label.toLowerCase()
      ? harnessOutcomeStatus
      : null;
  return (
    <Stack
      direction="row"
      spacing={1.25}
      alignItems="flex-start"
      sx={{
        p: 1.5,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
        ...sx,
      }}
    >
      <Iconify
        icon="solar:shield-cross-bold"
        width={16}
        sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }}
      />
      <Box minWidth={0} flex={1}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {`${label}. This scenario produced no verdict.`}
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          Nothing in this call counts against the agent.
        </Typography>
        {harness && (
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
          >
            {`Harness outcome: ${harness}`}
          </Typography>
        )}
      </Box>
      <SoonChip
        label="Cause" detail="Whether the simulated caller, the grader or the infrastructure stopped this call from getting a verdict."
        what="Why a call produced no verdict"
        size="tiny"
      />
    </Stack>
  );
}
UnmeasuredBanner.propTypes = {
  status: PropTypes.string,
  harnessOutcomeStatus: PropTypes.string,
  sx: PropTypes.object,
};

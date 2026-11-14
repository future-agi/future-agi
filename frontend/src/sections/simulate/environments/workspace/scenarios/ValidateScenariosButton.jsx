import PropTypes from "prop-types";
import { useState } from "react";
import { Button, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import {
  useScenarioValidation,
  useValidateScenarios,
} from "src/api/harness/scenarios";
import { SCENARIOS_COPY, scenarioToolbarButtonSx } from "./scenarios.constants";

// "Validate scenarios": proves every case of the default pair again. The button stays disabled
// while the validation runs and the list refreshes when it ends, so each case shows its new state.
export default function ValidateScenariosButton({ jobId, initialJobId = null }) {
  const [validationJobId, setValidationJobId] = useState(initialJobId);
  const [message, setMessage] = useState(null);
  const start = useValidateScenarios(jobId);
  const status = useScenarioValidation(jobId, validationJobId);
  const state = status.data?.state;
  const running = start.isPending || (validationJobId && (!state || state === "validating"));

  const onClick = async () => {
    setMessage(null);
    try {
      const result = await start.mutateAsync({});
      if (result.state === "unavailable") {
        setMessage(result.message);
        return;
      }
      setValidationJobId(result.job_id ?? null);
    } catch (error) {
      setMessage(error?.response?.data?.message ?? error?.message ?? null);
    }
  };

  const ended =
    state === "finished" && status.data?.proofs_recorded === false
      ? status.data?.message || SCENARIOS_COPY.validationNoResults
      : state === "failed"
        ? status.data?.message
        : null;

  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        disabled={Boolean(running)}
        onClick={onClick}
        startIcon={<Iconify icon="solar:shield-check-linear" width={16} />}
        sx={scenarioToolbarButtonSx}
      >
        {running ? SCENARIOS_COPY.validating : SCENARIOS_COPY.validate}
      </Button>
      {(message || ended) && (
        <Typography role="status" sx={{ typography: "s3", color: "text.subtitle" }}>
          {message || ended}
        </Typography>
      )}
    </Stack>
  );
}

ValidateScenariosButton.propTypes = {
  jobId: PropTypes.string,
  initialJobId: PropTypes.string,
};

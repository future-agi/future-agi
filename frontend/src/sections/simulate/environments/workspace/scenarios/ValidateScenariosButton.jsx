import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Button, Stack, Tooltip, Typography } from "@mui/material";
import { useSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import {
  useRunningScenarioValidation,
  useScenarioValidation,
  useValidateScenarios,
  useValidationOutcome,
} from "src/api/harness/scenarios";
import { SCENARIOS_COPY, scenarioToolbarButtonSx } from "./scenarios.constants";

// "Validate cases not yet proved": proves the default pair's cases that have no proof of their own yet,
// so a validation the window stopped carries on; with `keys`, only those cases, and the others keep their proofs. To
// prove every case again, the API takes `reprove_all` (a "Re-prove all" choice for the real UI). The
// button stays disabled while the validation runs and the list refreshes when it ends, so each case shows its new state.
// A validation already running when the page opens (started before a reload or on another page) is followed the same
// way, since a press would only return it.
// Validations whose end was said: a button mounted again (the list refreshing, a drawer opening) reads the same
// finished status, and must not raise the toast again over what is open.
const announced = new Set();

export default function ValidateScenariosButton({
  jobId,
  initialJobId = null,
  keys = null,
  label = SCENARIOS_COPY.validate,
}) {
  const { enqueueSnackbar } = useSnackbar();
  const [validationJobId, setValidationJobId] = useState(initialJobId);
  const [message, setMessage] = useState(null);
  const start = useValidateScenarios(jobId);
  const liveQuery = useRunningScenarioValidation(jobId);
  const live = liveQuery.data;
  const status = useScenarioValidation(jobId, validationJobId);
  const state = status.data?.state;
  const scoped = Boolean(keys?.length);
  // The running validation proves this button's cases: every case, or at least these.
  const covers =
    Boolean(live?.job_id) &&
    (!scoped ||
      !live.scenario_keys ||
      keys.every((key) => live.scenario_keys.includes(key)));
  const elsewhere = Boolean(live?.job_id) && !covers && !validationJobId;

  useEffect(() => {
    if (covers && !validationJobId) {
      setMessage(null);
      setValidationJobId(live.job_id);
    }
  }, [covers, live?.job_id, validationJobId]);

  // Its end is said once, as a toast with the app's standard placement and auto-hide, for the validation this button
  // followed; a finished one waits for the suite's counts, so it says what the counts line says.
  const recorded =
    state === "finished" && status.data?.proofs_recorded !== false;
  const outcome = useValidationOutcome(jobId, validationJobId, {
    enabled: recorded,
  });
  useEffect(() => {
    if (!validationJobId || !state || state === "validating") return;
    if (announced.has(validationJobId)) return;
    if (recorded && outcome.isPending) return;
    announced.add(validationJobId);
    if (recorded) {
      enqueueSnackbar(SCENARIOS_COPY.validationDone(outcome.data), {
        variant: "success",
      });
    } else {
      enqueueSnackbar(
        status.data?.message || SCENARIOS_COPY.validationNoResults,
        { variant: "error" },
      );
    }
  }, [
    enqueueSnackbar,
    outcome.data,
    outcome.isPending,
    recorded,
    state,
    status.data,
    validationJobId,
  ]);

  const running =
    start.isPending || (validationJobId && (!state || state === "validating"));
  // Nothing a press of the page's Validate would prove: said before the press, not after it.
  const nothing = !scoped && !running && !elsewhere && live?.to_prove === 0;

  const onClick = async () => {
    setMessage(null);
    try {
      const result = await start.mutateAsync(scoped ? { keys } : {});
      if (result.state === "unavailable" || result.state === "ready") {
        setMessage(result.message || SCENARIOS_COPY.nothingToValidate);
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
      : state === "failed" || state === "unavailable"
        ? status.data?.message
        : null;
  const said =
    message || ended || (elsewhere ? SCENARIOS_COPY.validationElsewhere : null);

  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Tooltip title={nothing ? live?.message || "" : ""}>
        <span>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            disabled={
              Boolean(running) || elsewhere || nothing || liveQuery.isLoading
            }
            onClick={onClick}
            startIcon={<Iconify icon="solar:shield-check-linear" width={16} />}
            sx={scenarioToolbarButtonSx}
          >
            {running
              ? Number.isInteger(status.data?.total) && status.data.total > 0
                ? SCENARIOS_COPY.validatingProgress(
                    status.data.proved,
                    status.data.total,
                  )
                : SCENARIOS_COPY.validating
              : nothing
                ? SCENARIOS_COPY.nothingToValidate
                : label}
          </Button>
        </span>
      </Tooltip>
      {said && (
        <Typography
          role="status"
          sx={{ typography: "s3", color: "text.subtitle" }}
        >
          {said}
        </Typography>
      )}
    </Stack>
  );
}

ValidateScenariosButton.propTypes = {
  jobId: PropTypes.string,
  initialJobId: PropTypes.string,
  keys: PropTypes.arrayOf(PropTypes.string),
  label: PropTypes.string,
};

import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import {
  Button,
  Fade,
  Stack,
  Tooltip,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { useSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import {
  useRunningScenarioValidation,
  useScenarioValidation,
  useValidateScenarios,
} from "src/api/simulate-environments/scenariosHooks";
import { LIFECYCLE_COPY, scenarioToolbarButtonSx } from "./scenarios.constants";

const COPY = LIFECYCLE_COPY.validate;

// Proves the default pair's scenarios in a sandbox: those with no proof yet, only `keys`, or every one with
// `reproveAll`. A validation already running when the page opens (before a reload, on another page) is followed
// instead of started again, since a press would only return it.
export default function ValidateScenariosButton({
  envId,
  keys = null,
  reproveAll = false,
  label = COPY.all,
  quiet = false,
}) {
  const { enqueueSnackbar } = useSnackbar();
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [validationJobId, setValidationJobId] = useState(null);
  const [message, setMessage] = useState(null);
  const start = useValidateScenarios(envId);
  const liveQuery = useRunningScenarioValidation(envId);
  const live = liveQuery.data;
  const status = useScenarioValidation(envId, validationJobId);
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

  // The end is said once, as a toast, by the button that started or followed it.
  const told = useRef(null);
  useEffect(() => {
    if (quiet || !validationJobId || !state || state === "validating") return;
    if (told.current === validationJobId) return;
    told.current = validationJobId;
    if (state === "finished" && status.data?.proofs_recorded !== false) {
      enqueueSnackbar(COPY.done(status.data?.proved, status.data?.total), {
        variant: "success",
      });
    } else {
      enqueueSnackbar(status.data?.message || COPY.noResults, {
        variant: "error",
      });
    }
  }, [enqueueSnackbar, quiet, state, status.data, validationJobId]);

  const running =
    start.isPending ||
    Boolean(validationJobId && (!state || state === "validating"));
  // Nothing a plain Validate would prove, said before the press. Re-prove all always has work.
  const nothing =
    !scoped && !reproveAll && !running && !elsewhere && live?.to_prove === 0;

  const onClick = async () => {
    setMessage(null);
    try {
      const result = await start.mutateAsync(
        scoped ? { keys } : reproveAll ? { reproveAll: true } : {},
      );
      if (result.state === "unavailable" || result.state === "ready") {
        setMessage(result.message || COPY.nothing);
        return;
      }
      told.current = null;
      setValidationJobId(result.job_id ?? null);
    } catch (error) {
      setMessage(error?.response?.data?.message ?? error?.message ?? null);
    }
  };

  const ended =
    state === "finished" && status.data?.proofs_recorded === false
      ? status.data?.message || COPY.noResults
      : state === "failed" || state === "unavailable"
        ? status.data?.message
        : null;
  const said = quiet
    ? null
    : message || ended || (elsewhere ? COPY.elsewhere : null);

  const text = running
    ? Number.isInteger(status.data?.total) && status.data.total > 0
      ? COPY.progress(status.data.proved, status.data.total)
      : COPY.validating
    : nothing
      ? COPY.nothing
      : label;

  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Tooltip
        arrow
        title={
          nothing ? live?.message || "" : reproveAll ? COPY.reproveAllHint : ""
        }
      >
        <span>
          <Button
            size="small"
            variant={quiet ? "text" : "outlined"}
            color="inherit"
            disabled={running || elsewhere || nothing || liveQuery.isLoading}
            onClick={onClick}
            startIcon={
              quiet ? null : (
                <Iconify icon="solar:shield-check-linear" width={16} />
              )
            }
            sx={scenarioToolbarButtonSx}
          >
            {quiet && running ? COPY.validating : text}
          </Button>
        </span>
      </Tooltip>
      <Fade
        in={Boolean(said)}
        timeout={reduceMotion ? 0 : undefined}
        unmountOnExit
      >
        <Typography
          role="status"
          sx={{ typography: "s3", color: "text.secondary" }}
        >
          {said}
        </Typography>
      </Fade>
    </Stack>
  );
}

ValidateScenariosButton.propTypes = {
  envId: PropTypes.string,
  keys: PropTypes.arrayOf(PropTypes.string),
  reproveAll: PropTypes.bool,
  label: PropTypes.string,
  quiet: PropTypes.bool,
};

import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  FormControl,
  FormControlLabel,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
import { useCreateConnection } from "src/api/integrations";
import { EXPORT_PLATFORMS, SYNC_INTERVALS, connectionName } from "../constants";
import { backButtonSx } from "../styles";
import { getErrorMessage, unwrapResponse } from "../utils";

// Mixpanel's /track API accepts events from the last 5 days; with an API
// secret the export uses /import, which accepts older ones.
const MIXPANEL_TRACK_DAYS = 5;
const MIXPANEL_TRACK_LIMIT = `Without an API secret, Mixpanel accepts request logs from the last ${MIXPANEL_TRACK_DAYS} days. Add the API secret to export older ones.`;
// Datadog's log intake accepts logs from the last 18 hours and drops older
// ones, so a Datadog export starts within them (the backend's
// DATADOG_LOG_MAX_AGE in futureagi/integrations/temporal/activities.py).
const DATADOG_LOG_HOURS = 18;
const DATADOG_LOG_LIMIT = `Datadog accepts logs from the last ${DATADOG_LOG_HOURS} hours. Pick a start within them.`;

// A datetime-local value (local time, minutes) for `date`.
const localDateTime = (date) => {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

export default function StepSyncSettings({
  data,
  onUpdate,
  onBack,
  onSuccess,
}) {
  const theme = useTheme();
  const { mutate: createConnection, isPending, error } = useCreateConnection();
  // An export starts from the last 24 hours or from a chosen date; it has no
  // "new only" choice and no end date.
  const isExport = EXPORT_PLATFORMS.includes(data.platform);
  const [backfillOption, setBackfillOption] = useState(
    isExport && data.backfillOption === "new_only"
      ? "all"
      : data.backfillOption || "all",
  );

  const [validationError, setValidationError] = useState(null);
  const trackOnly =
    data.platform === "mixpanel" && !data.credentials?.api_secret;
  const isDatadog = data.platform === "datadog";
  // Computed once: the earliest start the destination accepts, and why.
  const [earliest] = useState(() =>
    isDatadog
      ? new Date(Date.now() - DATADOG_LOG_HOURS * 60 * 60 * 1000)
      : trackOnly
        ? new Date(Date.now() - MIXPANEL_TRACK_DAYS * 24 * 60 * 60 * 1000)
        : null,
  );
  const startLimit = isDatadog
    ? DATADOG_LOG_LIMIT
    : trackOnly
      ? MIXPANEL_TRACK_LIMIT
      : null;

  const handleCreate = () => {
    setValidationError(null);

    if (backfillOption === "from_date" && !data.backfillFromDate) {
      setValidationError("Please select a start date.");
      return;
    }
    if (
      startLimit &&
      earliest &&
      backfillOption === "from_date" &&
      new Date(data.backfillFromDate) < earliest
    ) {
      setValidationError(startLimit);
      return;
    }
    if (
      !isExport &&
      backfillOption === "from_date" &&
      data.backfillFromDate &&
      data.backfillToDate &&
      new Date(data.backfillFromDate) >= new Date(data.backfillToDate)
    ) {
      setValidationError("End date must be after start date.");
      return;
    }

    const langfuseProjectName = data.selectedLangfuseProject?.name || "";

    const payload = {
      platform: data.platform,
      sync_interval_seconds: data.syncIntervalSeconds || 300,
      display_name:
        data.newProjectName ||
        langfuseProjectName ||
        connectionName(data.platform, data.credentials),
      external_project_name: langfuseProjectName || data.platform,
      backfill_option: backfillOption,
    };
    // Where the connection delivers, as its credentials step named it: the
    // Langfuse or PostHog address entered, the Datadog site's API host,
    // Mixpanel's API, the S3 bucket in its region under its prefix.
    if (data.hostUrl) payload.host_url = data.hostUrl;

    // Include credentials based on platform type
    if (data.credentials && Object.keys(data.credentials).length > 0) {
      payload.credentials = data.credentials;
    } else {
      payload.public_key = data.publicKey;
      payload.secret_key = data.secretKey;
    }

    if (data.caCertificate) {
      payload.ca_certificate = data.caCertificate;
    }

    if (data.futureAgiProjectId && data.futureAgiProjectId !== "__new__") {
      payload.project_id = data.futureAgiProjectId;
    } else if (data.newProjectName) {
      payload.new_project_name = data.newProjectName;
    }

    if (backfillOption === "from_date" && data.backfillFromDate) {
      payload.backfill_from_date = new Date(
        data.backfillFromDate,
      ).toISOString();
    }
    if (!isExport && backfillOption === "from_date" && data.backfillToDate) {
      payload.backfill_to_date = new Date(data.backfillToDate).toISOString();
    }

    createConnection(payload, {
      onSuccess: (response) => {
        const result = unwrapResponse(response);
        onSuccess(result?.id || null);
      },
    });
  };

  return (
    <Stack spacing={2.5}>
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        Configure sync settings
      </Typography>

      {data.selectedLangfuseProject && (
        <Alert severity="info" icon={false}>
          <Typography sx={{ typography: "s2", color: "text.primary" }}>
            Connecting Langfuse project:{" "}
            <strong>{data.selectedLangfuseProject.name}</strong>
          </Typography>
        </Alert>
      )}

      <FormControl>
        <Typography
          sx={{
            typography: "s1",
            fontWeight: "fontWeightMedium",
            color: "text.primary",
            mb: theme.spacing(1),
          }}
        >
          Sync Interval
        </Typography>
        <TextField
          select
          size="small"
          value={data.syncIntervalSeconds || 300}
          onChange={(e) =>
            onUpdate({ syncIntervalSeconds: Number(e.target.value) })
          }
          SelectProps={{ native: true }}
          fullWidth
        >
          {SYNC_INTERVALS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </TextField>
      </FormControl>

      <FormControl>
        <Typography
          sx={{
            typography: "s1",
            fontWeight: "fontWeightMedium",
            color: "text.primary",
            mb: theme.spacing(1),
          }}
        >
          Historical Data
        </Typography>
        <RadioGroup
          value={backfillOption}
          onChange={(e) => {
            // A refusal is about the start it was given: a new choice clears it.
            setValidationError(null);
            setBackfillOption(e.target.value);
            onUpdate({ backfillOption: e.target.value });
          }}
        >
          <FormControlLabel
            value="all"
            control={<Radio size="small" />}
            sx={{ ml: 0, alignItems: "flex-start" }}
            label={
              <Box sx={{ pt: 0.25 }}>
                <Typography sx={{ typography: "s1", color: "text.primary" }}>
                  {isDatadog
                    ? `Export the last ${DATADOG_LOG_HOURS} hours`
                    : isExport
                      ? "Export the last 24 hours"
                      : "Import all traces"}
                </Typography>
                <Typography sx={{ typography: "s2", color: "text.disabled" }}>
                  {isExport
                    ? "Then each new request log"
                    : data.totalTraces > 0
                      ? `~${data.totalTraces.toLocaleString()} traces`
                      : "All historical data"}
                </Typography>
              </Box>
            }
          />
          <FormControlLabel
            value="from_date"
            control={<Radio size="small" />}
            sx={{ ml: 0 }}
            label={
              <Typography sx={{ typography: "s1", color: "text.primary" }}>
                {isExport
                  ? "Export from a specific date"
                  : "Import from a specific date"}
              </Typography>
            }
          />
          {!isExport && (
            <FormControlLabel
              value="new_only"
              control={<Radio size="small" />}
              sx={{ ml: 0 }}
              label={
                <Typography sx={{ typography: "s1", color: "text.primary" }}>
                  Only import new traces going forward
                </Typography>
              }
            />
          )}
        </RadioGroup>
      </FormControl>

      {backfillOption === "from_date" && (
        <Stack direction="row" spacing={2}>
          <TextField
            label="Start Date & Time"
            type="datetime-local"
            size="small"
            value={data.backfillFromDate || ""}
            onChange={(e) => {
              setValidationError(null);
              onUpdate({ backfillFromDate: e.target.value });
            }}
            InputLabelProps={{ shrink: true }}
            inputProps={
              earliest && startLimit
                ? { min: localDateTime(earliest) }
                : undefined
            }
            helperText={startLimit || undefined}
            fullWidth
          />
          {!isExport && (
            <TextField
              label="End Date & Time"
              type="datetime-local"
              size="small"
              value={data.backfillToDate || ""}
              onChange={(e) => onUpdate({ backfillToDate: e.target.value })}
              InputLabelProps={{ shrink: true }}
              fullWidth
            />
          )}
        </Stack>
      )}

      {validationError && <Alert severity="warning">{validationError}</Alert>}

      {error && (
        <Alert severity="error">
          {getErrorMessage(error, "Failed to create integration")}
        </Alert>
      )}

      <Box display="flex" justifyContent="space-between">
        <Button
          size="small"
          variant="outlined"
          onClick={onBack}
          startIcon={<Iconify icon="formkit:left" width={16} height={16} />}
          sx={backButtonSx}
        >
          Back
        </Button>
        <LoadingButton
          variant="contained"
          color="primary"
          size="small"
          loading={isPending}
          onClick={handleCreate}
          sx={{ fontWeight: 500 }}
        >
          Connect Integration
        </LoadingButton>
      </Box>
    </Stack>
  );
}

StepSyncSettings.propTypes = {
  data: PropTypes.object.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onBack: PropTypes.func.isRequired,
  onSuccess: PropTypes.func.isRequired,
};

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
import { SYNC_INTERVALS } from "../constants";
import { backButtonSx } from "../styles";
import { getErrorMessage, unwrapResponse } from "../utils";

export default function StepSyncSettings({
  data,
  onUpdate,
  onBack,
  onSuccess,
}) {
  const theme = useTheme();
  const { mutate: createConnection, isPending, error } = useCreateConnection();
  const [backfillOption, setBackfillOption] = useState(
    data.backfillOption || "all",
  );

  const [validationError, setValidationError] = useState(null);

  const handleCreate = () => {
    setValidationError(null);

    if (backfillOption === "from_date" && !data.backfillFromDate) {
      setValidationError("Please select a start date.");
      return;
    }
    if (
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
      host_url: data.hostUrl || "",
      sync_interval_seconds: data.syncIntervalSeconds || 300,
      display_name: data.newProjectName || langfuseProjectName || data.platform,
      external_project_name: langfuseProjectName || data.platform,
      backfill_option: backfillOption,
    };

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
    if (backfillOption === "from_date" && data.backfillToDate) {
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
                  Import all traces
                </Typography>
                <Typography sx={{ typography: "s2", color: "text.disabled" }}>
                  {data.totalTraces > 0
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
                Import from a specific date
              </Typography>
            }
          />
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
        </RadioGroup>
      </FormControl>

      {backfillOption === "from_date" && (
        <Stack direction="row" spacing={2}>
          <TextField
            label="Start Date & Time"
            type="datetime-local"
            size="small"
            value={data.backfillFromDate || ""}
            onChange={(e) => onUpdate({ backfillFromDate: e.target.value })}
            InputLabelProps={{ shrink: true }}
            fullWidth
          />
          <TextField
            label="End Date & Time"
            type="datetime-local"
            size="small"
            value={data.backfillToDate || ""}
            onChange={(e) => onUpdate({ backfillToDate: e.target.value })}
            InputLabelProps={{ shrink: true }}
            fullWidth
          />
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

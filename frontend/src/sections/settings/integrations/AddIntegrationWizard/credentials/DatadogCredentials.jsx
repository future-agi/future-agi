import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
import { useValidateCredentials } from "src/api/integrations";
import { backButtonSx } from "../../styles";
import { getErrorMessage, unwrapResponse } from "../../utils";
import { DATADOG_SITES } from "../../constants";

export default function DatadogCredentials({ data, onUpdate, onNext, onBack }) {
  const [site, setSite] = useState(data.credentials?.site || "us1");
  const [apiKey, setApiKey] = useState(data.credentials?.api_key || "");
  const [appKey, setAppKey] = useState(data.credentials?.app_key || "");
  const [showApiKey, setShowApiKey] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});

  const { mutate: validate, isPending, error } = useValidateCredentials();

  const handleValidate = () => {
    const errors = {};
    if (!apiKey.trim()) errors.apiKey = "API key is required";
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});

    const credentials = {
      api_key: apiKey.trim(),
      site,
      app_key: appKey.trim(),
    };

    validate(
      { platform: "datadog", credentials },
      {
        onSuccess: (response) => {
          unwrapResponse(response);
          onUpdate({
            credentials,
            hostUrl: `https://api.${DATADOG_SITES.find((s) => s.value === site)?.domain || "datadoghq.com"}`,
          });
          onNext();
        },
      },
    );
  };

  return (
    <Stack spacing={2.5}>
      <Typography sx={{ typography: "s1", color: "text.primary" }}>
        Enter your Datadog API credentials
      </Typography>

      <TextField
        select
        label="Datadog Site"
        value={site}
        onChange={(e) => setSite(e.target.value)}
        size="small"
        fullWidth
        helperText="Select the Datadog region where your account is hosted"
      >
        {DATADOG_SITES.map((s) => (
          <MenuItem key={s.value} value={s.value}>
            {s.label}
          </MenuItem>
        ))}
      </TextField>

      <TextField
        label="API Key"
        value={apiKey}
        onChange={(e) => {
          setApiKey(e.target.value);
          setFieldErrors((prev) => ({ ...prev, apiKey: undefined }));
        }}
        size="small"
        fullWidth
        required
        type={showApiKey ? "text" : "password"}
        error={Boolean(fieldErrors.apiKey)}
        helperText={fieldErrors.apiKey || "Your Datadog API key"}
        placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        InputProps={{
          endAdornment: (
            <IconButton onClick={() => setShowApiKey(!showApiKey)} edge="end">
              <Iconify
                icon={showApiKey ? "solar:eye-bold" : "solar:eye-closed-bold"}
              />
            </IconButton>
          ),
        }}
      />

      <TextField
        label="Application Key (Optional)"
        value={appKey}
        onChange={(e) => setAppKey(e.target.value)}
        size="small"
        fullWidth
        type="password"
        helperText="Optional — needed for creating Datadog dashboard templates"
        placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      />

      {error && (
        <Alert severity="error">
          {getErrorMessage(error, "Failed to validate credentials")}
        </Alert>
      )}

      <Box display="flex" justifyContent="space-between">
        <Button
          size="small"
          variant="outlined"
          onClick={onBack}
          startIcon={
            <Iconify icon="solar:arrow-left-linear" width={16} height={16} />
          }
          sx={backButtonSx}
        >
          Back
        </Button>
        <LoadingButton
          variant="contained"
          color="primary"
          size="small"
          loading={isPending}
          onClick={handleValidate}
        >
          Validate & Continue
        </LoadingButton>
      </Box>
    </Stack>
  );
}

DatadogCredentials.propTypes = {
  data: PropTypes.object.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
  onBack: PropTypes.func.isRequired,
};

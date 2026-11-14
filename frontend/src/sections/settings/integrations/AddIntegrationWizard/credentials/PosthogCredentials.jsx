import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
import { useValidateCredentials } from "src/api/integrations";
import { backButtonSx } from "../../styles";
import { getErrorMessage, unwrapResponse } from "../../utils";

const DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com";

const POSTHOG_REGIONS = [
  { value: "https://us.i.posthog.com", label: "US Cloud" },
  { value: "https://eu.i.posthog.com", label: "EU Cloud" },
];

export default function PosthogCredentials({ data, onUpdate, onNext, onBack }) {
  const [hostUrl, setHostUrl] = useState(
    data.hostUrl && data.hostUrl !== "https://cloud.langfuse.com"
      ? data.hostUrl
      : DEFAULT_POSTHOG_HOST,
  );
  const [apiKey, setApiKey] = useState(data.credentials?.api_key || "");
  const [showApiKey, setShowApiKey] = useState(false);
  const [isSelfHosted, setIsSelfHosted] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});

  const { mutate: validate, isPending, error } = useValidateCredentials();

  const handleValidate = () => {
    const errors = {};
    if (!apiKey.trim()) errors.apiKey = "Project API key is required";
    if (isSelfHosted && !hostUrl.trim())
      errors.hostUrl = "Host URL is required";
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});

    const credentials = { api_key: apiKey.trim() };
    const resolvedHost = isSelfHosted ? hostUrl.trim() : hostUrl;

    validate(
      { platform: "posthog", host_url: resolvedHost, credentials },
      {
        onSuccess: (response) => {
          unwrapResponse(response);
          onUpdate({
            credentials,
            hostUrl: resolvedHost,
          });
          onNext();
        },
      },
    );
  };

  return (
    <Stack spacing={2.5}>
      <Typography sx={{ typography: "s1", color: "text.primary" }}>
        Enter your PostHog credentials
      </Typography>

      {!isSelfHosted ? (
        <TextField
          select
          label="PostHog Region"
          value={hostUrl}
          onChange={(e) => setHostUrl(e.target.value)}
          size="small"
          fullWidth
          SelectProps={{ native: true }}
          helperText="Select the region where your PostHog project is hosted"
        >
          {POSTHOG_REGIONS.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </TextField>
      ) : (
        <TextField
          label="Host URL"
          value={hostUrl}
          onChange={(e) => {
            setHostUrl(e.target.value);
            setFieldErrors((prev) => ({ ...prev, hostUrl: undefined }));
          }}
          size="small"
          fullWidth
          required
          error={Boolean(fieldErrors.hostUrl)}
          helperText={fieldErrors.hostUrl || "Your self-hosted PostHog URL"}
          placeholder="https://posthog.yourcompany.com"
        />
      )}

      <Button
        size="small"
        variant="text"
        onClick={() => {
          setIsSelfHosted(!isSelfHosted);
          if (!isSelfHosted) {
            setHostUrl("");
          } else {
            setHostUrl(DEFAULT_POSTHOG_HOST);
          }
        }}
        sx={{ alignSelf: "flex-start" }}
      >
        {isSelfHosted ? "Use PostHog Cloud" : "Using self-hosted PostHog?"}
      </Button>

      <TextField
        label="Project API Key"
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
        helperText={
          fieldErrors.apiKey ||
          "Found in PostHog → Project Settings → Project API Key"
        }
        placeholder="phc_..."
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

PosthogCredentials.propTypes = {
  data: PropTypes.object.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
  onBack: PropTypes.func.isRequired,
};

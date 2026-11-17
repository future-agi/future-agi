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
import {
  useValidateCredentials,
  useCreateConnection,
} from "src/api/integrations";
import { backButtonSx } from "../../styles";
import { getErrorMessage, unwrapResponse } from "../../utils";

export default function LinearCredentials({
  data,
  onUpdate,
  onNext,
  onBack,
  onSuccess,
}) {
  const [apiKey, setApiKey] = useState(data.credentials?.api_key || "");
  const [showKey, setShowKey] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});

  const {
    mutate: validate,
    isPending: validating,
    error,
  } = useValidateCredentials();
  const { mutate: createConnection, isPending: creating } =
    useCreateConnection();

  const isPending = validating || creating;

  const handleValidate = () => {
    const errors = {};
    if (!apiKey.trim()) errors.apiKey = "API key is required";
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});

    const credentials = { api_key: apiKey.trim() };

    validate(
      { platform: "linear", credentials },
      {
        onSuccess: (response) => {
          unwrapResponse(response);
          onUpdate({
            credentials,
            hostUrl: "https://api.linear.app",
          });

          // Create connection immediately — Linear has no sync settings
          const payload = {
            platform: "linear",
            host_url: "https://api.linear.app",
            credentials,
            display_name: "Linear",
            external_project_name: "linear",
            sync_interval_seconds: 300,
            backfill_option: "new_only",
          };

          createConnection(payload, {
            onSuccess: (createResponse) => {
              const result = unwrapResponse(createResponse);
              if (onSuccess) {
                onSuccess(result?.id || null);
              } else {
                onNext();
              }
            },
          });
        },
      },
    );
  };

  return (
    <Stack spacing={2.5}>
      <Typography sx={{ typography: "s1", color: "text.primary" }}>
        Enter your Linear API key
      </Typography>

      <Alert severity="info" icon={false}>
        <Typography sx={{ typography: "s2", color: "text.primary" }}>
          Create a personal API key in Linear &rarr; Settings &rarr; Account
          &rarr; Security &amp; Access &rarr; Personal API keys. Grant at least{" "}
          <strong>Issues: Write</strong> permission.
        </Typography>
      </Alert>

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
        type={showKey ? "text" : "password"}
        error={Boolean(fieldErrors.apiKey)}
        helperText={fieldErrors.apiKey || "Linear personal API key"}
        placeholder="lin_api_..."
        InputProps={{
          endAdornment: (
            <IconButton onClick={() => setShowKey(!showKey)} edge="end">
              <Iconify
                icon={showKey ? "solar:eye-bold" : "solar:eye-closed-bold"}
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
          {isPending ? "Connecting…" : "Connect Linear"}
        </LoadingButton>
      </Box>
    </Stack>
  );
}

LinearCredentials.propTypes = {
  data: PropTypes.object.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
  onBack: PropTypes.func.isRequired,
  onSuccess: PropTypes.func,
};

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

export default function MixpanelCredentials({
  data,
  onUpdate,
  onNext,
  onBack,
}) {
  const [projectToken, setProjectToken] = useState(
    data.credentials?.project_token || "",
  );
  const [apiSecret, setApiSecret] = useState(
    data.credentials?.api_secret || "",
  );
  const [showSecret, setShowSecret] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});

  const { mutate: validate, isPending, error } = useValidateCredentials();

  const handleValidate = () => {
    const errors = {};
    if (!projectToken.trim()) errors.projectToken = "Project token is required";
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});

    const credentials = { project_token: projectToken.trim() };
    if (apiSecret.trim()) {
      credentials.api_secret = apiSecret.trim();
    }

    validate(
      { platform: "mixpanel", credentials },
      {
        onSuccess: (response) => {
          unwrapResponse(response);
          onUpdate({
            credentials,
            hostUrl: "https://api.mixpanel.com",
          });
          onNext();
        },
      },
    );
  };

  return (
    <Stack spacing={2.5}>
      <Typography sx={{ typography: "s1", color: "text.primary" }}>
        Enter your Mixpanel credentials
      </Typography>

      <TextField
        label="Project Token"
        value={projectToken}
        onChange={(e) => {
          setProjectToken(e.target.value);
          setFieldErrors((prev) => ({ ...prev, projectToken: undefined }));
        }}
        size="small"
        fullWidth
        required
        error={Boolean(fieldErrors.projectToken)}
        helperText={
          fieldErrors.projectToken ||
          "Found in Mixpanel → Settings → Project Settings → Project Token"
        }
        placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      />

      <TextField
        label="API Secret (Optional)"
        value={apiSecret}
        onChange={(e) => setApiSecret(e.target.value)}
        size="small"
        fullWidth
        type={showSecret ? "text" : "password"}
        helperText="Optional legacy authentication. Your project token also supports historical imports."
        placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        InputProps={{
          endAdornment: (
            <IconButton onClick={() => setShowSecret(!showSecret)} edge="end">
              <Iconify
                icon={showSecret ? "solar:eye-bold" : "solar:eye-closed-bold"}
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
          onClick={handleValidate}
          sx={{ fontWeight: 500 }}
        >
          Validate & Continue
        </LoadingButton>
      </Box>
    </Stack>
  );
}

MixpanelCredentials.propTypes = {
  data: PropTypes.object.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
  onBack: PropTypes.func.isRequired,
};

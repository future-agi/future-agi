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

export default function PagerdutyCredentials({
  data,
  onUpdate,
  onNext,
  onBack,
}) {
  const [routingKey, setRoutingKey] = useState(
    data.credentials?.routing_key || "",
  );
  const [showKey, setShowKey] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});

  const { mutate: validate, isPending, error } = useValidateCredentials();

  const handleValidate = () => {
    const errors = {};
    if (!routingKey.trim()) errors.routingKey = "Routing key is required";
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});

    const credentials = { routing_key: routingKey.trim() };

    validate(
      { platform: "pagerduty", credentials },
      {
        onSuccess: (response) => {
          unwrapResponse(response);
          onUpdate({
            credentials,
            hostUrl: "https://events.pagerduty.com",
          });
          onNext();
        },
      },
    );
  };

  return (
    <Stack spacing={2.5}>
      <Typography sx={{ typography: "s1", color: "text.primary" }}>
        Enter your PagerDuty credentials
      </Typography>

      <Alert severity="info" icon={false}>
        <Typography sx={{ typography: "s2", color: "text.primary" }}>
          Use an <strong>Events API v2</strong> integration key from your
          PagerDuty service. Find it in PagerDuty &rarr; Services &rarr; your
          service &rarr; Integrations &rarr; Events API v2.
        </Typography>
      </Alert>

      <TextField
        label="Routing Key"
        value={routingKey}
        onChange={(e) => {
          setRoutingKey(e.target.value);
          setFieldErrors((prev) => ({ ...prev, routingKey: undefined }));
        }}
        size="small"
        fullWidth
        required
        type={showKey ? "text" : "password"}
        error={Boolean(fieldErrors.routingKey)}
        helperText={
          fieldErrors.routingKey || "Events API v2 integration/routing key"
        }
        placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
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
          Validate & Continue
        </LoadingButton>
      </Box>
    </Stack>
  );
}

PagerdutyCredentials.propTypes = {
  data: PropTypes.object.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
  onBack: PropTypes.func.isRequired,
};

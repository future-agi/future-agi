import { useState } from "react";
import PropTypes from "prop-types";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import z from "zod";
import Iconify from "src/components/iconify";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useValidateCredentials } from "src/api/integrations";
import { backButtonSx } from "../styles";
import {
  DEFAULT_LANGFUSE_HOST,
  CUSTOM_CREDENTIAL_PLATFORMS,
} from "../constants";
import { getErrorMessage, unwrapResponse } from "../utils";
import DatadogCredentials from "./credentials/DatadogCredentials";
import PosthogCredentials from "./credentials/PosthogCredentials";
import PagerdutyCredentials from "./credentials/PagerdutyCredentials";
import MixpanelCredentials from "./credentials/MixpanelCredentials";
import CloudStorageCredentials from "./credentials/CloudStorageCredentials";
import LinearCredentials from "./credentials/LinearCredentials";
import MessageQueueCredentials from "./credentials/MessageQueueCredentials";

const credentialsSchema = z.object({
  host_url: z
    .string()
    .min(1, "Host URL is required")
    .url("Must be a valid URL"),
  public_key: z.string().min(1, "Public key is required"),
  secret_key: z.string().min(1, "Secret key is required"),
  ca_certificate: z.string().optional(),
});

export default function StepCredentials({
  data,
  onUpdate,
  onNext,
  onBack,
  onSuccess,
}) {
  // Dispatch to platform-specific credential forms
  if (CUSTOM_CREDENTIAL_PLATFORMS.includes(data.platform)) {
    switch (data.platform) {
      case "datadog":
        return (
          <DatadogCredentials
            data={data}
            onUpdate={onUpdate}
            onNext={onNext}
            onBack={onBack}
          />
        );
      case "posthog":
        return (
          <PosthogCredentials
            data={data}
            onUpdate={onUpdate}
            onNext={onNext}
            onBack={onBack}
          />
        );
      case "pagerduty":
        return (
          <PagerdutyCredentials
            data={data}
            onUpdate={onUpdate}
            onNext={onNext}
            onBack={onBack}
          />
        );
      case "mixpanel":
        return (
          <MixpanelCredentials
            data={data}
            onUpdate={onUpdate}
            onNext={onNext}
            onBack={onBack}
          />
        );
      case "cloud_storage":
        return (
          <CloudStorageCredentials
            data={data}
            onUpdate={onUpdate}
            onNext={onNext}
            onBack={onBack}
          />
        );
      case "message_queue":
        return (
          <MessageQueueCredentials
            data={data}
            onUpdate={onUpdate}
            onNext={onNext}
            onBack={onBack}
          />
        );
      case "linear":
        return (
          <LinearCredentials
            data={data}
            onUpdate={onUpdate}
            onNext={onNext}
            onBack={onBack}
            onSuccess={onSuccess}
          />
        );
      default:
        break;
    }
  }

  // Default: public_key / secret_key credentials
  return (
    <LangfuseCredentials
      data={data}
      onUpdate={onUpdate}
      onNext={onNext}
      onBack={onBack}
    />
  );
}

StepCredentials.propTypes = {
  data: PropTypes.object.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
  onBack: PropTypes.func.isRequired,
  onSuccess: PropTypes.func,
};

function LangfuseCredentials({ data, onUpdate, onNext, onBack }) {
  const [showSecret, setShowSecret] = useState(false);
  const { mutate: validate, isPending, error } = useValidateCredentials();

  const { control, handleSubmit } = useForm({
    resolver: zodResolver(credentialsSchema),
    defaultValues: {
      host_url: data.hostUrl || DEFAULT_LANGFUSE_HOST,
      public_key: data.publicKey || "",
      secret_key: data.secretKey || "",
      ca_certificate: data.caCertificate || "",
    },
  });

  const onSubmit = (formData) => {
    validate(
      { platform: data.platform, ...formData },
      {
        onSuccess: (response) => {
          const result = unwrapResponse(response);
          const projects = result.projects || [];
          onUpdate({
            hostUrl: formData.host_url,
            publicKey: formData.public_key,
            secretKey: formData.secret_key,
            caCertificate: formData.ca_certificate,
            langfuseProjects: projects,
            selectedLangfuseProject: projects.length === 1 ? projects[0] : null,
            totalTraces: result.total_traces || 0,
          });
          onNext();
        },
      },
    );
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <Stack spacing={2.5}>
        <Typography sx={{ typography: "s1", color: "text.primary" }}>
          Enter your Langfuse API credentials
        </Typography>

        <FormTextFieldV2
          control={control}
          fieldName="host_url"
          label="Host URL"
          fullWidth
          required
          size="small"
        />

        <FormTextFieldV2
          control={control}
          fieldName="public_key"
          label="Public Key"
          fullWidth
          required
          size="small"
          placeholder="pk-lf-..."
        />

        <FormTextFieldV2
          control={control}
          fieldName="secret_key"
          label="Secret Key"
          fullWidth
          required
          size="small"
          type={showSecret ? "text" : "password"}
          placeholder="sk-lf-..."
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

        <Accordion
          sx={{
            "&:before": { display: "none" },
            boxShadow: "none",
            border: "1px solid",
            borderColor: "action.hover",
            borderRadius: (t) => `${t.spacing(1)} !important`,
            "&.Mui-expanded": { margin: 0 },
          }}
        >
          <AccordionSummary
            expandIcon={
              <Iconify
                icon="solar:alt-arrow-down-linear"
                width={20}
                height={20}
                sx={{ color: "text.disabled" }}
              />
            }
            sx={{
              minHeight: "auto",
              py: 1,
              "& .MuiAccordionSummary-content": {
                margin: 0,
                "&.Mui-expanded": { margin: 0 },
              },
            }}
          >
            <Typography sx={{ typography: "s1", color: "text.secondary" }}>
              Advanced Settings
            </Typography>
          </AccordionSummary>
          <AccordionDetails sx={{ pt: 0 }}>
            <FormTextFieldV2
              control={control}
              fieldName="ca_certificate"
              label="CA Certificate (PEM)"
              fullWidth
              multiline
              size="small"
              rows={4}
              placeholder="Paste PEM certificate for self-hosted instances..."
            />
          </AccordionDetails>
        </Accordion>

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
            type="submit"
            variant="contained"
            color="primary"
            size="small"
            loading={isPending}
          >
            Validate & Continue
          </LoadingButton>
        </Box>
      </Stack>
    </form>
  );
}

LangfuseCredentials.propTypes = {
  data: PropTypes.object.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
  onBack: PropTypes.func.isRequired,
};

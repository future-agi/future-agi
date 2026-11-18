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
import Section from "src/components/section";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
import { useValidateCredentials } from "src/api/integrations";
import { backButtonSx } from "../../styles";
import { getErrorMessage, unwrapResponse } from "../../utils";

const AWS_REGIONS = [
  "us-east-1",
  "us-east-2",
  "us-west-1",
  "us-west-2",
  "eu-west-1",
  "eu-west-2",
  "eu-central-1",
  "ap-southeast-1",
  "ap-northeast-1",
];

const SUB_PLATFORMS = [
  { id: "sqs", label: "Amazon SQS", icon: "mdi:aws" },
  { id: "pubsub", label: "Google Pub/Sub", icon: "mdi:google-cloud" },
];

export default function MessageQueueCredentials({
  data,
  onUpdate,
  onNext,
  onBack,
}) {
  const [subPlatform, setSubPlatform] = useState(
    data.credentials?.sub_platform || "",
  );

  // SQS fields
  const [sqsQueueUrl, setSqsQueueUrl] = useState(
    data.credentials?.queue_url || "",
  );
  const [sqsRegion, setSqsRegion] = useState(
    data.credentials?.region || "us-east-1",
  );
  const [sqsAccessKey, setSqsAccessKey] = useState(
    data.credentials?.access_key_id || "",
  );
  const [sqsSecretKey, setSqsSecretKey] = useState(
    data.credentials?.secret_access_key || "",
  );
  const [showSqsSecret, setShowSqsSecret] = useState(false);

  // Pub/Sub fields
  const [pubsubTopic, setPubsubTopic] = useState(
    data.credentials?.topic_path || "",
  );
  const [pubsubProjectId, setPubsubProjectId] = useState(
    data.credentials?.project_id || "",
  );
  const [pubsubServiceAccount, setPubsubServiceAccount] = useState(
    data.credentials?.service_account_json || "",
  );

  const [fieldErrors, setFieldErrors] = useState({});
  const { mutate: validate, isPending, error } = useValidateCredentials();

  const buildCredentials = () => {
    if (subPlatform === "sqs") {
      return {
        sub_platform: "sqs",
        queue_url: sqsQueueUrl.trim(),
        region: sqsRegion,
        access_key_id: sqsAccessKey.trim(),
        secret_access_key: sqsSecretKey.trim(),
      };
    }
    if (subPlatform === "pubsub") {
      return {
        sub_platform: "pubsub",
        topic_path: pubsubTopic.trim(),
        project_id: pubsubProjectId.trim(),
        service_account_json: pubsubServiceAccount.trim(),
      };
    }
    return {};
  };

  const validateFields = () => {
    const errors = {};
    if (!subPlatform) {
      errors.subPlatform = "Please select a queue provider";
      return errors;
    }
    if (subPlatform === "sqs") {
      if (!sqsQueueUrl.trim()) errors.sqsQueueUrl = "Queue URL is required";
      if (!sqsAccessKey.trim()) errors.sqsAccessKey = "Access key is required";
      if (!sqsSecretKey.trim()) errors.sqsSecretKey = "Secret key is required";
    }
    if (subPlatform === "pubsub") {
      if (!pubsubTopic.trim()) errors.pubsubTopic = "Topic path is required";
      if (!pubsubServiceAccount.trim())
        errors.pubsubServiceAccount = "Service account JSON is required";
    }
    return errors;
  };

  const handleValidate = () => {
    const errors = validateFields();
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});

    const credentials = buildCredentials();

    validate(
      { platform: "message_queue", credentials },
      {
        onSuccess: (response) => {
          unwrapResponse(response);
          onUpdate({ credentials, hostUrl: "" });
          onNext();
        },
      },
    );
  };

  return (
    <Stack spacing={2.5}>
      <Typography sx={{ typography: "s1", color: "text.primary" }}>
        Configure message queue destination
      </Typography>

      {/* Sub-platform selector */}
      <Stack direction="row" spacing={1.5}>
        {SUB_PLATFORMS.map((sp) => (
          <Section
            key={sp.id}
            variant="panel"
            onClick={() => {
              setSubPlatform(sp.id);
              setFieldErrors({});
            }}
            sx={{
              flex: 1,
              textAlign: "center",
              borderColor: subPlatform === sp.id ? "text.primary" : "divider",
            }}
          >
            <Iconify icon={sp.icon} width={28} sx={{ mb: 0.5 }} />
            <Typography sx={{ typography: "s2", fontWeight: 500 }}>
              {sp.label}
            </Typography>
          </Section>
        ))}
      </Stack>

      {fieldErrors.subPlatform && (
        <Alert severity="warning">{fieldErrors.subPlatform}</Alert>
      )}

      {/* SQS fields */}
      {subPlatform === "sqs" && (
        <Stack spacing={2}>
          <TextField
            label="Queue URL"
            value={sqsQueueUrl}
            onChange={(e) => setSqsQueueUrl(e.target.value)}
            size="small"
            fullWidth
            required
            error={Boolean(fieldErrors.sqsQueueUrl)}
            helperText={fieldErrors.sqsQueueUrl}
            placeholder="https://sqs.us-east-1.amazonaws.com/123456789/my-queue"
          />
          <TextField
            select
            label="Region"
            value={sqsRegion}
            onChange={(e) => setSqsRegion(e.target.value)}
            size="small"
            fullWidth
          >
            {AWS_REGIONS.map((r) => (
              <MenuItem key={r} value={r}>
                {r}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            label="Access Key ID"
            value={sqsAccessKey}
            onChange={(e) => setSqsAccessKey(e.target.value)}
            size="small"
            fullWidth
            required
            error={Boolean(fieldErrors.sqsAccessKey)}
            helperText={fieldErrors.sqsAccessKey}
            placeholder="AKIA..."
          />
          <TextField
            label="Secret Access Key"
            value={sqsSecretKey}
            onChange={(e) => setSqsSecretKey(e.target.value)}
            size="small"
            fullWidth
            required
            type={showSqsSecret ? "text" : "password"}
            error={Boolean(fieldErrors.sqsSecretKey)}
            helperText={fieldErrors.sqsSecretKey}
            InputProps={{
              endAdornment: (
                <IconButton
                  onClick={() => setShowSqsSecret(!showSqsSecret)}
                  edge="end"
                >
                  <Iconify
                    icon={
                      showSqsSecret ? "solar:eye-bold" : "solar:eye-closed-bold"
                    }
                  />
                </IconButton>
              ),
            }}
          />
        </Stack>
      )}

      {/* Pub/Sub fields */}
      {subPlatform === "pubsub" && (
        <Stack spacing={2}>
          <TextField
            label="Topic Path"
            value={pubsubTopic}
            onChange={(e) => setPubsubTopic(e.target.value)}
            size="small"
            fullWidth
            required
            error={Boolean(fieldErrors.pubsubTopic)}
            helperText={
              fieldErrors.pubsubTopic ||
              "Full path: projects/{project-id}/topics/{topic-name}"
            }
            placeholder="projects/my-project/topics/agentcc-logs"
          />
          <TextField
            label="GCP Project ID"
            value={pubsubProjectId}
            onChange={(e) => setPubsubProjectId(e.target.value)}
            size="small"
            fullWidth
            placeholder="my-gcp-project"
          />
          <TextField
            label="Service Account JSON"
            value={pubsubServiceAccount}
            onChange={(e) => setPubsubServiceAccount(e.target.value)}
            size="small"
            fullWidth
            required
            multiline
            rows={4}
            error={Boolean(fieldErrors.pubsubServiceAccount)}
            helperText={
              fieldErrors.pubsubServiceAccount ||
              "Paste the full service account key JSON"
            }
            placeholder='{"type": "service_account", ...}'
          />
        </Stack>
      )}

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

MessageQueueCredentials.propTypes = {
  data: PropTypes.object.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
  onBack: PropTypes.func.isRequired,
};

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
  { id: "s3", label: "Amazon S3", icon: "mdi:aws" },
  { id: "azure_blob", label: "Azure Blob", icon: "mdi:microsoft-azure" },
  { id: "gcs", label: "Google Cloud Storage", icon: "mdi:google-cloud" },
];

export default function CloudStorageCredentials({
  data,
  onUpdate,
  onNext,
  onBack,
}) {
  const [subPlatform, setSubPlatform] = useState(
    data.credentials?.sub_platform || "",
  );

  // S3 fields
  const [s3Bucket, setS3Bucket] = useState(data.credentials?.bucket || "");
  const [s3Region, setS3Region] = useState(
    data.credentials?.region || "us-east-1",
  );
  const [s3AccessKey, setS3AccessKey] = useState(
    data.credentials?.access_key_id || "",
  );
  const [s3SecretKey, setS3SecretKey] = useState(
    data.credentials?.secret_access_key || "",
  );
  const [s3Prefix, setS3Prefix] = useState(data.credentials?.prefix || "");
  const [showS3Secret, setShowS3Secret] = useState(false);

  // Azure fields
  const [azureContainer, setAzureContainer] = useState(
    data.credentials?.container || "",
  );
  const [azureConnStr, setAzureConnStr] = useState(
    data.credentials?.connection_string || "",
  );
  const [azurePrefix, setAzurePrefix] = useState(
    data.credentials?.prefix || "",
  );
  const [showAzureConn, setShowAzureConn] = useState(false);

  // GCS fields
  const [gcsBucket, setGcsBucket] = useState(data.credentials?.bucket || "");
  const [gcsServiceAccount, setGcsServiceAccount] = useState(
    data.credentials?.service_account_json || "",
  );
  const [gcsPrefix, setGcsPrefix] = useState(data.credentials?.prefix || "");

  const [fieldErrors, setFieldErrors] = useState({});
  const { mutate: validate, isPending, error } = useValidateCredentials();

  const buildCredentials = () => {
    if (subPlatform === "s3") {
      return {
        sub_platform: "s3",
        bucket: s3Bucket.trim(),
        region: s3Region,
        access_key_id: s3AccessKey.trim(),
        secret_access_key: s3SecretKey.trim(),
        prefix: s3Prefix.trim(),
      };
    }
    if (subPlatform === "azure_blob") {
      return {
        sub_platform: "azure_blob",
        container: azureContainer.trim(),
        connection_string: azureConnStr.trim(),
        prefix: azurePrefix.trim(),
      };
    }
    if (subPlatform === "gcs") {
      return {
        sub_platform: "gcs",
        bucket: gcsBucket.trim(),
        service_account_json: gcsServiceAccount.trim(),
        prefix: gcsPrefix.trim(),
      };
    }
    return {};
  };

  const validateFields = () => {
    const errors = {};
    if (!subPlatform) {
      errors.subPlatform = "Please select a storage provider";
      return errors;
    }
    if (subPlatform === "s3") {
      if (!s3Bucket.trim()) errors.s3Bucket = "Bucket name is required";
      if (!s3AccessKey.trim()) errors.s3AccessKey = "Access key is required";
      if (!s3SecretKey.trim()) errors.s3SecretKey = "Secret key is required";
    }
    if (subPlatform === "azure_blob") {
      if (!azureContainer.trim())
        errors.azureContainer = "Container name is required";
      if (!azureConnStr.trim())
        errors.azureConnStr = "Connection string is required";
    }
    if (subPlatform === "gcs") {
      if (!gcsBucket.trim()) errors.gcsBucket = "Bucket name is required";
      if (!gcsServiceAccount.trim())
        errors.gcsServiceAccount = "Service account JSON is required";
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
      { platform: "cloud_storage", credentials },
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
        Configure cloud storage destination
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

      {/* S3 fields */}
      {subPlatform === "s3" && (
        <Stack spacing={2}>
          <TextField
            label="Bucket Name"
            value={s3Bucket}
            onChange={(e) => setS3Bucket(e.target.value)}
            size="small"
            fullWidth
            required
            error={Boolean(fieldErrors.s3Bucket)}
            helperText={fieldErrors.s3Bucket}
            placeholder="my-agentcc-logs"
          />
          <TextField
            select
            label="Region"
            value={s3Region}
            onChange={(e) => setS3Region(e.target.value)}
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
            value={s3AccessKey}
            onChange={(e) => setS3AccessKey(e.target.value)}
            size="small"
            fullWidth
            required
            error={Boolean(fieldErrors.s3AccessKey)}
            helperText={fieldErrors.s3AccessKey}
            placeholder="AKIA..."
          />
          <TextField
            label="Secret Access Key"
            value={s3SecretKey}
            onChange={(e) => setS3SecretKey(e.target.value)}
            size="small"
            fullWidth
            required
            type={showS3Secret ? "text" : "password"}
            error={Boolean(fieldErrors.s3SecretKey)}
            helperText={fieldErrors.s3SecretKey}
            InputProps={{
              endAdornment: (
                <IconButton
                  onClick={() => setShowS3Secret(!showS3Secret)}
                  edge="end"
                >
                  <Iconify
                    icon={
                      showS3Secret ? "solar:eye-bold" : "solar:eye-closed-bold"
                    }
                  />
                </IconButton>
              ),
            }}
          />
          <TextField
            label="Key Prefix (Optional)"
            value={s3Prefix}
            onChange={(e) => setS3Prefix(e.target.value)}
            size="small"
            fullWidth
            helperText="e.g. agentcc/production"
            placeholder="agentcc/"
          />
        </Stack>
      )}

      {/* Azure Blob fields */}
      {subPlatform === "azure_blob" && (
        <Stack spacing={2}>
          <TextField
            label="Container Name"
            value={azureContainer}
            onChange={(e) => setAzureContainer(e.target.value)}
            size="small"
            fullWidth
            required
            error={Boolean(fieldErrors.azureContainer)}
            helperText={fieldErrors.azureContainer}
            placeholder="agentcc-logs"
          />
          <TextField
            label="Connection String"
            value={azureConnStr}
            onChange={(e) => setAzureConnStr(e.target.value)}
            size="small"
            fullWidth
            required
            type={showAzureConn ? "text" : "password"}
            error={Boolean(fieldErrors.azureConnStr)}
            helperText={
              fieldErrors.azureConnStr ||
              "Azure Storage connection string from the portal"
            }
            placeholder="DefaultEndpointsProtocol=https;..."
            InputProps={{
              endAdornment: (
                <IconButton
                  onClick={() => setShowAzureConn(!showAzureConn)}
                  edge="end"
                >
                  <Iconify
                    icon={
                      showAzureConn ? "solar:eye-bold" : "solar:eye-closed-bold"
                    }
                  />
                </IconButton>
              ),
            }}
          />
          <TextField
            label="Blob Prefix (Optional)"
            value={azurePrefix}
            onChange={(e) => setAzurePrefix(e.target.value)}
            size="small"
            fullWidth
            helperText="e.g. agentcc/production"
            placeholder="agentcc/"
          />
        </Stack>
      )}

      {/* GCS fields */}
      {subPlatform === "gcs" && (
        <Stack spacing={2}>
          <TextField
            label="Bucket Name"
            value={gcsBucket}
            onChange={(e) => setGcsBucket(e.target.value)}
            size="small"
            fullWidth
            required
            error={Boolean(fieldErrors.gcsBucket)}
            helperText={fieldErrors.gcsBucket}
            placeholder="my-agentcc-logs"
          />
          <TextField
            label="Service Account JSON"
            value={gcsServiceAccount}
            onChange={(e) => setGcsServiceAccount(e.target.value)}
            size="small"
            fullWidth
            required
            multiline
            rows={4}
            error={Boolean(fieldErrors.gcsServiceAccount)}
            helperText={
              fieldErrors.gcsServiceAccount ||
              "Paste the full service account key JSON"
            }
            placeholder='{"type": "service_account", ...}'
          />
          <TextField
            label="Object Prefix (Optional)"
            value={gcsPrefix}
            onChange={(e) => setGcsPrefix(e.target.value)}
            size="small"
            fullWidth
            helperText="e.g. agentcc/production"
            placeholder="agentcc/"
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

CloudStorageCredentials.propTypes = {
  data: PropTypes.object.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
  onBack: PropTypes.func.isRequired,
};

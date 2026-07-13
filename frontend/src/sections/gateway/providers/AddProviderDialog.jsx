import React, { useState, useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Stack,
  Alert,
  Chip,
  Autocomplete,
  MenuItem,
  Checkbox,
  CircularProgress,
  Typography,
  Box,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import {
  useUpdateProvider,
  useFetchProviderModels,
} from "./hooks/useGatewayConfig";

const PROVIDER_PRESETS = {
  openai: {
    label: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    apiFormat: "openai",
    keyPlaceholder: "sk-...",
    supportedFormats: ["openai", "anthropic"],
  },
  anthropic: {
    label: "Anthropic",
    baseUrl: "https://api.anthropic.com",
    apiFormat: "anthropic",
    keyPlaceholder: "sk-ant-...",
    supportedFormats: ["openai", "anthropic"],
  },
  google: {
    label: "Google (Gemini)",
    baseUrl: "https://generativelanguage.googleapis.com",
    apiFormat: "google",
    keyPlaceholder: "AIza...",
    supportedFormats: ["openai", "google"],
  },
  azure: {
    label: "Azure OpenAI",
    baseUrl: "",
    apiFormat: "azure",
    keyPlaceholder: "Enter your Azure API key",
    supportedFormats: ["openai", "anthropic"],
  },
  cohere: {
    label: "Cohere",
    baseUrl: "https://api.cohere.ai/compatibility/v1",
    apiFormat: "openai",
    keyPlaceholder: "Enter your Cohere API key",
    supportedFormats: ["openai"],
  },
  bedrock: {
    label: "AWS Bedrock",
    baseUrl: "",
    apiFormat: "bedrock",
    authType: "aws",
    supportedFormats: ["anthropic"],
  },
  groq: {
    label: "Groq",
    baseUrl: "https://api.groq.com/openai/v1",
    apiFormat: "openai",
    keyPlaceholder: "gsk_...",
    supportedFormats: ["openai"],
  },
  together: {
    label: "Together AI",
    baseUrl: "https://api.together.xyz/v1",
    apiFormat: "openai",
    keyPlaceholder: "Enter your Together API key",
    supportedFormats: ["openai"],
  },
  fireworks: {
    label: "Fireworks AI",
    baseUrl: "https://api.fireworks.ai/inference/v1",
    apiFormat: "openai",
    keyPlaceholder: "Enter your Fireworks API key",
    supportedFormats: ["openai"],
  },
  mistral: {
    label: "Mistral AI",
    baseUrl: "https://api.mistral.ai/v1",
    apiFormat: "openai",
    keyPlaceholder: "Enter your Mistral API key",
    supportedFormats: ["openai"],
  },
  perplexity: {
    label: "Perplexity",
    baseUrl: "https://api.perplexity.ai",
    apiFormat: "openai",
    keyPlaceholder: "pplx-...",
    supportedFormats: ["openai"],
  },
  deepseek: {
    label: "DeepSeek",
    baseUrl: "https://api.deepseek.com/v1",
    apiFormat: "openai",
    keyPlaceholder: "Enter your DeepSeek API key",
    supportedFormats: ["openai"],
  },
  openrouter: {
    label: "OpenRouter",
    baseUrl: "https://openrouter.ai/api/v1",
    apiFormat: "openai",
    keyPlaceholder: "sk-or-...",
    supportedFormats: ["openai"],
  },
  replicate: {
    label: "Replicate",
    baseUrl: "https://api.replicate.com/v1",
    apiFormat: "openai",
    keyPlaceholder: "r8_...",
    supportedFormats: ["openai"],
  },
  huggingface: {
    label: "HuggingFace",
    baseUrl: "https://api-inference.huggingface.co",
    apiFormat: "openai",
    keyPlaceholder: "hf_...",
    supportedFormats: ["openai"],
  },
  deepinfra: {
    label: "DeepInfra",
    baseUrl: "https://api.deepinfra.com/v1/openai",
    apiFormat: "openai",
    keyPlaceholder: "Enter your DeepInfra API key",
    supportedFormats: ["openai"],
  },
  cerebras: {
    label: "Cerebras",
    baseUrl: "https://api.cerebras.ai/v1",
    apiFormat: "openai",
    keyPlaceholder: "Enter your Cerebras API key",
    supportedFormats: ["openai"],
  },
  ollama: {
    label: "Ollama (self-hosted)",
    baseUrl: "http://localhost:11434",
    apiFormat: "openai",
    keyPlaceholder: "ollama",
    supportedFormats: ["openai"],
  },
  vertex_ai: {
    label: "Vertex AI",
    baseUrl: "",
    apiFormat: "google",
    keyPlaceholder: "Enter your Google access token",
    supportedFormats: ["openai", "google"],
  },
  custom: {
    label: "Custom / Self-hosted",
    baseUrl: "",
    apiFormat: "openai",
    keyPlaceholder: "Enter API key",
    supportedFormats: ["openai", "anthropic", "google"],
  },
};

const AWS_REGIONS = [
  { value: "us-east-1", label: "US East (N. Virginia)" },
  { value: "us-west-2", label: "US West (Oregon)" },
  { value: "eu-west-1", label: "Europe (Ireland)" },
  { value: "eu-central-1", label: "Europe (Frankfurt)" },
  { value: "ap-southeast-1", label: "Asia Pacific (Singapore)" },
  { value: "ap-northeast-1", label: "Asia Pacific (Tokyo)" },
  { value: "ap-south-1", label: "Asia Pacific (Mumbai)" },
  { value: "ca-central-1", label: "Canada (Central)" },
  { value: "sa-east-1", label: "South America (São Paulo)" },
];

const PROVIDER_OPTIONS = Object.entries(PROVIDER_PRESETS).map(
  ([key, preset]) => ({
    value: key,
    label: preset.label,
  }),
);

const API_FORMATS = [
  "openai",
  "anthropic",
  "cohere",
  "google",
  "azure",
  "bedrock",
];

const AddProviderDialog = ({ open, onClose, gatewayId, provider }) => {
  const isEditMode = Boolean(provider);

  const [name, setName] = useState("openai");
  const [baseUrl, setBaseUrl] = useState(PROVIDER_PRESETS.openai.baseUrl);
  const [apiKey, setApiKey] = useState("");
  const [apiFormat, setApiFormat] = useState("openai");
  const [models, setModels] = useState([]);
  const [timeoutVal, setTimeoutVal] = useState("");
  const [maxConcurrent, setMaxConcurrent] = useState("");

  // AWS Bedrock credentials
  const [awsAccessKeyId, setAwsAccessKeyId] = useState("");
  const [awsSecretAccessKey, setAwsSecretAccessKey] = useState("");
  const [awsRegion, setAwsRegion] = useState("us-east-1");
  const [awsSessionToken, setAwsSessionToken] = useState("");

  // Validation state
  const [errors, setErrors] = useState({});

  const updateProvider = useUpdateProvider();
  const fetchModels = useFetchProviderModels();
  const [modelOptions, setModelOptions] = useState([]);
  const [fetchError, setFetchError] = useState("");
  const [hasFetched, setHasFetched] = useState(false);

  const doFetchModels = useCallback(
    ({ providerName, url, key, format }) => {
      setFetchError("");
      setHasFetched(false);
      fetchModels.mutate(
        providerName
          ? { providerName }
          : { baseUrl: url, apiKey: key, apiFormat: format },
        {
          onSuccess: (result) => {
            const fetched = result?.models || [];
            if (fetched.length === 0 && result?.error) {
              setFetchError(result.error);
            }
            setModelOptions(fetched);
            setHasFetched(true);
          },
          onError: (err) => {
            setFetchError(err?.message || "Failed to fetch models");
            setModelOptions([]);
            setHasFetched(true);
          },
        },
      );
    },
    [fetchModels],
  );

  // Edit mode: populate form from existing provider
  useEffect(() => {
    if (open && isEditMode && provider) {
      const c = provider.config || {};
      setName(provider.name || "");
      setBaseUrl(c.base_url ?? c.baseUrl ?? "");
      setApiKey("");
      setApiFormat(c.api_format ?? c.apiFormat ?? "openai");
      setModels(Array.isArray(c.models) ? c.models : []);
      setTimeoutVal(c.default_timeout ?? c.defaultTimeout ?? "");
      setMaxConcurrent(
        c.max_concurrent != null
          ? String(c.max_concurrent ?? c.maxConcurrent ?? "")
          : "",
      );
      setErrors({});
      doFetchModels({ providerName: provider.name });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, isEditMode, provider]);

  // Create mode: set defaults when dialog opens
  useEffect(() => {
    if (open && !isEditMode) {
      resetForm();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, isEditMode]);

  const isAwsAuth = PROVIDER_PRESETS[name]?.authType === "aws";

  // Create mode: auto-fetch when API key is entered (debounced)
  // Skip auto-fetch for AWS providers (Bedrock models must be entered manually)
  useEffect(() => {
    if (isEditMode || isAwsAuth) return;
    if (!apiKey.trim()) {
      setModelOptions([]);
      setFetchError("");
      setHasFetched(false);
      return;
    }
    // Don't fetch if base URL is required but empty (azure, custom)
    const preset = PROVIDER_PRESETS[name];
    if (preset && !preset.baseUrl && !baseUrl.trim()) return;

    const timer = setTimeout(() => {
      doFetchModels({ url: baseUrl, key: apiKey, format: apiFormat });
    }, 600);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey, baseUrl, apiFormat, isEditMode, isAwsAuth]);

  const resetForm = () => {
    const defaultPreset = PROVIDER_PRESETS.openai;
    setName("openai");
    setBaseUrl(defaultPreset.baseUrl);
    setApiKey("");
    setApiFormat(defaultPreset.apiFormat);
    setModels([]);
    setModelOptions([]);
    setFetchError("");
    setHasFetched(false);
    setTimeoutVal("");
    setMaxConcurrent("");
    setAwsAccessKeyId("");
    setAwsSecretAccessKey("");
    setAwsRegion("us-east-1");
    setAwsSessionToken("");
    setErrors({});
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  const handleProviderChange = (newName) => {
    setName(newName);
    const preset = PROVIDER_PRESETS[newName];
    if (preset) {
      if (preset.authType === "aws") {
        setBaseUrl(`https://bedrock-runtime.${awsRegion}.amazonaws.com`);
      } else {
        setBaseUrl(preset.baseUrl);
      }
      // Reset apiFormat to preset default if the current value isn't supported
      setApiFormat((prev) =>
        preset.supportedFormats && !preset.supportedFormats.includes(prev)
          ? preset.apiFormat
          : prev,
      );
    }
    // Clear models since provider changed
    setModels([]);
    setModelOptions([]);
    setFetchError("");
    setHasFetched(false);
    setErrors({});
  };

  const handleAwsRegionChange = (region) => {
    setAwsRegion(region);
    setBaseUrl(`https://bedrock-runtime.${region}.amazonaws.com`);
  };

  const handleSelectAll = () => {
    if (models.length === modelOptions.length) {
      setModels([]);
    } else {
      setModels([...modelOptions]);
    }
  };

  const validate = () => {
    const newErrors = {};

    if (!name.trim()) {
      newErrors.name = "Provider name is required";
    }

    if (isAwsAuth) {
      // AWS-specific validation
      if (!isEditMode && !awsAccessKeyId.trim()) {
        newErrors.awsAccessKeyId = "AWS Access Key ID is required";
      }
      if (!isEditMode && !awsSecretAccessKey.trim()) {
        newErrors.awsSecretAccessKey = "AWS Secret Access Key is required";
      }
    } else {
      // Base URL validation — required for providers without a preset URL
      const preset = PROVIDER_PRESETS[name];
      const needsBaseUrl = !preset || !preset.baseUrl;
      if (needsBaseUrl && !baseUrl.trim()) {
        newErrors.baseUrl = "Base URL is required for this provider";
      }
      if (baseUrl.trim() && !baseUrl.startsWith("http")) {
        newErrors.baseUrl = "Base URL must start with http:// or https://";
      }

      // API key required for new providers
      if (!isEditMode && !apiKey.trim()) {
        newErrors.apiKey = "API key is required";
      }

      // Key validation — only block if fetch completed with zero models
      if (
        !isEditMode &&
        apiKey.trim() &&
        hasFetched &&
        modelOptions.length === 0
      ) {
        newErrors.apiKey =
          "Invalid API key or unreachable provider — please check your credentials";
      }
    }

    // Require at least one model selected
    if (models.length === 0) {
      newErrors.models = "Select at least one model";
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSave = () => {
    if (!validate()) return;

    const config = { base_url: baseUrl, api_format: apiFormat };
    if (isAwsAuth) {
      if (awsAccessKeyId) config.aws_access_key_id = awsAccessKeyId;
      if (awsSecretAccessKey) config.aws_secret_access_key = awsSecretAccessKey;
      if (awsRegion) config.aws_region = awsRegion;
      if (awsSessionToken) config.aws_session_token = awsSessionToken;
    } else if (apiKey) {
      config.api_key = apiKey;
    }
    if (models.length > 0) config.models = models;
    if (timeoutVal) config.default_timeout = timeoutVal;
    if (maxConcurrent) config.max_concurrent = Number(maxConcurrent);

    updateProvider.mutate(
      { gatewayId, name, config },
      {
        onSuccess: () => {
          enqueueSnackbar(
            isEditMode
              ? `Provider "${name}" updated`
              : `Provider "${name}" added`,
            { variant: "success" },
          );
          handleClose();
        },
        onError: () => {
          enqueueSnackbar(
            isEditMode ? "Failed to update provider" : "Failed to add provider",
            { variant: "error" },
          );
        },
      },
    );
  };

  const allSelected =
    modelOptions.length > 0 && models.length === modelOptions.length;

  const preset = PROVIDER_PRESETS[name] || PROVIDER_PRESETS.custom;

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>{isEditMode ? "Edit Provider" : "Add Provider"}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} mt={1}>
          {/* Provider Name — dropdown for common providers */}
          {isEditMode ? (
            <TextField
              label="Provider Name"
              fullWidth
              required
              value={name}
              disabled
              helperText="Provider name cannot be changed"
            />
          ) : (
            <TextField
              label="Provider"
              select
              fullWidth
              required
              value={name}
              onChange={(e) => handleProviderChange(e.target.value)}
              error={!!errors.name}
              helperText={errors.name}
            >
              {PROVIDER_OPTIONS.map((opt) => (
                <MenuItem key={opt.value} value={opt.value}>
                  {opt.label}
                </MenuItem>
              ))}
            </TextField>
          )}

          {isAwsAuth ? (
            <>
              <TextField
                label="AWS Region"
                select
                fullWidth
                required
                value={awsRegion}
                onChange={(e) => handleAwsRegionChange(e.target.value)}
              >
                {AWS_REGIONS.map((r) => (
                  <MenuItem key={r.value} value={r.value}>
                    {r.label} ({r.value})
                  </MenuItem>
                ))}
              </TextField>

              <TextField
                label="AWS Access Key ID"
                fullWidth
                required={!isEditMode}
                value={awsAccessKeyId}
                onChange={(e) => {
                  setAwsAccessKeyId(e.target.value);
                  setErrors((prev) => ({ ...prev, awsAccessKeyId: undefined }));
                }}
                placeholder={
                  isEditMode ? "Leave blank to keep current" : "AKIA..."
                }
                error={!!errors.awsAccessKeyId}
                helperText={errors.awsAccessKeyId}
              />

              <TextField
                label="AWS Secret Access Key"
                fullWidth
                required={!isEditMode}
                type="password"
                autoComplete="off"
                value={awsSecretAccessKey}
                onChange={(e) => {
                  setAwsSecretAccessKey(e.target.value);
                  setErrors((prev) => ({
                    ...prev,
                    awsSecretAccessKey: undefined,
                  }));
                }}
                placeholder={
                  isEditMode
                    ? "Leave blank to keep current"
                    : "Enter secret key"
                }
                error={!!errors.awsSecretAccessKey}
                helperText={errors.awsSecretAccessKey}
              />

              <TextField
                label="AWS Session Token (optional)"
                fullWidth
                type="password"
                autoComplete="off"
                value={awsSessionToken}
                onChange={(e) => setAwsSessionToken(e.target.value)}
                placeholder="For temporary credentials only"
                helperText="Only needed for temporary AWS credentials (STS)"
              />
            </>
          ) : (
            <>
              <TextField
                label="Base URL"
                fullWidth
                required={!preset.baseUrl}
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={preset.baseUrl || "https://your-endpoint.com"}
                error={!!errors.baseUrl}
                helperText={
                  errors.baseUrl ||
                  (preset.baseUrl
                    ? "Auto-filled from provider preset"
                    : "Required — enter your endpoint URL")
                }
              />

              <TextField
                label="API Key"
                fullWidth
                required={!isEditMode}
                type="password"
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value);
                  setErrors((prev) => ({ ...prev, apiKey: undefined }));
                }}
                placeholder={
                  isEditMode
                    ? "Leave blank to keep current key"
                    : preset.keyPlaceholder
                }
                error={!!errors.apiKey}
                helperText={errors.apiKey}
              />
            </>
          )}

          {(() => {
            const currentPreset = PROVIDER_PRESETS[name];
            const visibleFormats =
              currentPreset?.supportedFormats?.length
                ? API_FORMATS.filter((f) =>
                    currentPreset.supportedFormats.includes(f),
                  )
                : API_FORMATS;
            const isFiltered =
              visibleFormats.length < API_FORMATS.length;
            const isSingleOption = visibleFormats.length === 1;
            return (
              <TextField
                label="API Format"
                select
                fullWidth
                value={apiFormat}
                onChange={(e) => setApiFormat(e.target.value)}
                disabled={isSingleOption}
                helperText={
                  isFiltered
                    ? "Restricted to compatible formats for this provider"
                    : undefined
                }
              >
                {visibleFormats.map((f) => (
                  <MenuItem key={f} value={f}>
                    {f}
                  </MenuItem>
                ))}
              </TextField>
            );
          })()}

          <Box>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              mb={0.5}
            >
              <Typography variant="subtitle2" color="text.secondary">
                Models
                {fetchModels.isPending && (
                  <CircularProgress size={14} sx={{ ml: 1 }} />
                )}
                {hasFetched && modelOptions.length > 0 && (
                  <Typography
                    component="span"
                    variant="caption"
                    color="text.disabled"
                    sx={{ ml: 1 }}
                  >
                    ({modelOptions.length} available)
                  </Typography>
                )}
              </Typography>
              {modelOptions.length > 0 && (
                <Button size="small" onClick={handleSelectAll}>
                  {allSelected ? "Deselect All" : "Select All"}
                </Button>
              )}
            </Stack>

            <Autocomplete
              multiple
              freeSolo
              autoSelect
              disableCloseOnSelect
              options={modelOptions}
              value={models}
              onChange={(_, val) => {
                setModels(val.map((v) => v.trim()).filter(Boolean));
                setErrors((prev) => ({ ...prev, models: undefined }));
              }}
              renderOption={(props, option, { selected }) => (
                <li {...props} key={option}>
                  <Checkbox size="small" checked={selected} sx={{ mr: 1 }} />
                  {option}
                </li>
              )}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => (
                  <Chip
                    label={option}
                    size="small"
                    {...getTagProps({ index })}
                    key={option}
                  />
                ))
              }
              renderInput={(params) => (
                <TextField
                  {...params}
                  placeholder={
                    fetchModels.isPending
                      ? "Fetching models..."
                      : modelOptions.length > 0
                        ? "Select models..."
                        : isAwsAuth
                          ? "Type or paste comma-separated model IDs"
                          : "Enter API key to load models, or type manually"
                  }
                  size="small"
                  onPaste={(e) => {
                    const pasted = e.clipboardData.getData("text");
                    if (!/[,\n]/.test(pasted)) return;
                    e.preventDefault();
                    const tokens = pasted
                      .split(/[,\n]/)
                      .map((s) => s.trim())
                      .filter(Boolean);
                    if (tokens.length === 0) return;
                    setModels((prev) =>
                      Array.from(new Set([...prev, ...tokens])),
                    );
                    setErrors((p) => ({ ...p, models: undefined }));
                  }}
                />
              )}
            />
            {fetchError && (
              <Alert
                severity="warning"
                variant="outlined"
                sx={{ mt: 1, py: 0 }}
              >
                {fetchError}
              </Alert>
            )}
            {errors.models && (
              <Typography variant="caption" color="error" sx={{ mt: 0.5 }}>
                {errors.models}
              </Typography>
            )}
          </Box>

          <Stack direction="row" spacing={2}>
            <TextField
              label="Timeout"
              value={timeoutVal}
              onChange={(e) => setTimeoutVal(e.target.value)}
              placeholder="30s"
              sx={{ flex: 1 }}
            />
            <TextField
              label="Max Concurrent"
              type="number"
              value={maxConcurrent}
              onChange={(e) => setMaxConcurrent(e.target.value)}
              placeholder="10"
              sx={{ flex: 1 }}
            />
          </Stack>

          {updateProvider.isError && (
            <Alert severity="error">
              {updateProvider.error?.message ||
                (isEditMode
                  ? "Failed to update provider"
                  : "Failed to add provider")}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={!name.trim() || updateProvider.isPending}
        >
          {updateProvider.isPending
            ? isEditMode
              ? "Saving..."
              : "Adding..."
            : isEditMode
              ? "Save Changes"
              : "Add Provider"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

AddProviderDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  gatewayId: PropTypes.string,
  provider: PropTypes.shape({
    name: PropTypes.string.isRequired,
    config: PropTypes.object,
  }),
};

export default AddProviderDialog;

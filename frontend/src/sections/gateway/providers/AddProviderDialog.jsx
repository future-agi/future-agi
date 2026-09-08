import React, { useState, useEffect, useCallback, useRef } from "react";
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
  AlertTitle,
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
import { parseTimeoutSeconds } from "./utils";

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

// The API types a provider's models as free-form JSON, so an entry need not be
// a string — and a non-string one takes the dialog down when it reaches state.
const toModelId = (value) => {
  if (typeof value === "string") return value.trim();
  if (value && typeof value === "object") {
    return String(value.id ?? value.name ?? value.model ?? "").trim();
  }
  return value == null ? "" : String(value).trim();
};

const normalizeModels = (list) =>
  Array.isArray(list) ? list.map(toModelId).filter(Boolean) : [];

const KEY_FETCH_FAILED =
  "Couldn't load any models with this key. Check that it is valid for this " +
  "provider, or add model IDs manually below.";

const STORED_KEY_UNVERIFIED =
  "Couldn't list this provider's models with the stored key. Enter a new API " +
  "key, or add model IDs manually below, to save changes.";

// Summary labels, in form order so the summary reads like the dialog.
const FIELD_LABELS = {
  name: "Provider Name",
  awsAccessKeyId: "AWS Access Key ID",
  awsSecretAccessKey: "AWS Secret Access Key",
  baseUrl: "Base URL",
  apiKey: "API Key",
  timeout: "Timeout",
};

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
  const [summaryDismissed, setSummaryDismissed] = useState(false);
  // Every model ID a listing has offered in this dialog, plus the ones already
  // stored on the provider. Anything selected outside it was typed by hand.
  const offeredModels = useRef(new Set());
  // The body scrolls, so a failed Save has to bring the summary back into view.
  const contentRef = useRef(null);

  const updateProvider = useUpdateProvider();
  const fetchModels = useFetchProviderModels();
  const [modelOptions, setModelOptions] = useState([]);
  const [fetchError, setFetchError] = useState("");
  const [hasFetched, setHasFetched] = useState(false);
  // Kept out of `errors` so a failed fetch does not raise the Save-time summary.
  const [keyFetchError, setKeyFetchError] = useState("");
  // A debounced fetch is armed but has not fired yet.
  const [fetchScheduled, setFetchScheduled] = useState(false);
  const fetchSeqRef = useRef(0);

  // What the fetch-by-name concluded, so clearing a typed key restores it.
  const storedKeyResult = useRef(null);

  // The last fetch made with a key typed into the form.
  const typedFetchSeqRef = useRef(0);

  const doFetchModels = useCallback(
    ({ providerName, url, key, format }) => {
      const seq = fetchSeqRef.current + 1;
      fetchSeqRef.current = seq;
      if (!providerName) typedFetchSeqRef.current = seq;
      const isStale = () => fetchSeqRef.current !== seq;
      setFetchError("");
      setKeyFetchError("");
      setHasFetched(false);
      // A by-name fetch uses the stored credential; no key here to blame.
      const blameKey = !providerName;
      fetchModels.mutate(
        providerName
          ? { providerName }
          : { baseUrl: url, apiKey: key, apiFormat: format },
        {
          onSuccess: (result) => {
            if (isStale()) return;
            const fetched = normalizeModels(result?.models);
            const emptyReason =
              fetched.length === 0
                ? result?.error || "Provider returned no models"
                : "";
            setFetchError(emptyReason);
            if (emptyReason && blameKey) setKeyFetchError(KEY_FETCH_FAILED);
            fetched.forEach((m) => offeredModels.current.add(m));
            setModelOptions(fetched);
            setHasFetched(true);
            if (providerName) {
              storedKeyResult.current = {
                options: fetched,
                error: emptyReason,
                hasFetched: true,
              };
            }
          },
          onError: (err) => {
            if (isStale()) return;
            // A request that failed reached no verdict on the key, so
            // `hasFetched` stays false and the API Key field is left alone.
            const message = err?.message || "Failed to fetch models";
            setFetchError(message);
            setModelOptions([]);
            setHasFetched(false);
            if (providerName) {
              storedKeyResult.current = {
                options: [],
                error: message,
                hasFetched: false,
              };
            }
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
      setModels(normalizeModels(c.models));
      const timeoutRaw = c.default_timeout ?? c.defaultTimeout;
      setTimeoutVal(timeoutRaw != null ? String(timeoutRaw) : "");
      setMaxConcurrent(
        c.max_concurrent != null
          ? String(c.max_concurrent ?? c.maxConcurrent ?? "")
          : "",
      );
      setErrors({});
      setSummaryDismissed(false);
      offeredModels.current = new Set(normalizeModels(c.models));
      storedKeyResult.current = null;
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

  // Auto-fetch when an API key is entered (debounced). Edit mode included: a
  // typed key replaces the stored one, so it has to list models before Save.
  // Skip auto-fetch for AWS providers (Bedrock models must be entered manually)
  useEffect(() => {
    if (isAwsAuth) {
      setFetchScheduled(false);
      return;
    }
    if (!apiKey.trim()) {
      setFetchScheduled(false);
      // Blank in edit mode means "keep the stored key", so restore its verdict:
      // a typed key's result left standing blames the stored one for it.
      if (isEditMode) {
        // Abandon a typed-key fetch still in flight, whose late response would
        // overwrite the restore. Only that one: this also runs on mount, where
        // the request in flight is the by-name fetch just made.
        if (fetchSeqRef.current === typedFetchSeqRef.current) {
          fetchSeqRef.current += 1;
        }
        const snapshot = storedKeyResult.current;
        if (snapshot) {
          setModelOptions(snapshot.options);
          setFetchError(snapshot.error);
          setHasFetched(snapshot.hasFetched);
          setKeyFetchError("");
        }
        return;
      }
      setModelOptions([]);
      setFetchError("");
      setKeyFetchError("");
      setHasFetched(false);
      return;
    }
    // Don't fetch if base URL is required but empty (azure, custom)
    const preset = PROVIDER_PRESETS[name];
    if (preset && !preset.baseUrl && !baseUrl.trim()) {
      setFetchScheduled(false);
      return;
    }

    // Held from the first keystroke: the debounce is long enough to click in.
    setFetchScheduled(true);
    const timer = setTimeout(() => {
      setFetchScheduled(false);
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
    setKeyFetchError("");
    setFetchScheduled(false);
    setHasFetched(false);
    setTimeoutVal("");
    setMaxConcurrent("");
    setAwsAccessKeyId("");
    setAwsSecretAccessKey("");
    setAwsRegion("us-east-1");
    setAwsSessionToken("");
    setErrors({});
    setSummaryDismissed(false);
    offeredModels.current = new Set();
    storedKeyResult.current = null;
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
    setKeyFetchError("");
    setHasFetched(false);
    setErrors({});
    setSummaryDismissed(false);
    offeredModels.current = new Set();
    storedKeyResult.current = null;
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

  // A model the provider never offered was typed in by hand, overriding an
  // empty listing: the azure and custom presets are probed with a plain
  // `{base_url}/models` a working endpoint need not serve, so an empty list
  // there is no proof the key is bad. Derived, so removing the chip re-arms
  // the gate.
  const manualModels = models.some((m) => !offeredModels.current.has(m));

  const validate = (timeoutSeconds) => {
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
        newErrors.baseUrl =
          "This provider has no preset endpoint — enter its base URL, e.g. https://your-endpoint.com";
      }
      if (baseUrl.trim() && !/^https?:\/\//i.test(baseUrl.trim())) {
        newErrors.baseUrl = `Base URL must start with http:// or https:// (got "${baseUrl.trim()}")`;
      }

      // API key required for new providers
      if (!isEditMode && !apiKey.trim()) {
        newErrors.apiKey = "API key is required";
      }

      // Only a listing that came back empty counts, and only until models are
      // entered by hand.
      if (
        apiKey.trim() &&
        hasFetched &&
        modelOptions.length === 0 &&
        !manualModels
      ) {
        newErrors.apiKey = KEY_FETCH_FAILED;
      }
    }

    if (timeoutVal.trim() && timeoutSeconds === null) {
      newErrors.timeout = `Timeout must be a whole number of seconds, e.g. 30 or 30s (got "${timeoutVal.trim()}")`;
    }

    setErrors(newErrors);
    setSummaryDismissed(false);
    return Object.keys(newErrors).length === 0;
  };

  const handleSave = () => {
    const timeoutSeconds = parseTimeoutSeconds(timeoutVal);
    if (!validate(timeoutSeconds)) {
      // scrollTo is missing in jsdom — never throw on a cosmetic scroll.
      contentRef.current?.scrollTo?.({ top: 0, behavior: "smooth" });
      return;
    }

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
    if (timeoutSeconds !== null) config.default_timeout = timeoutSeconds;
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

  const modelsLoading = fetchScheduled || fetchModels.isPending;

  // A typed key answered with an empty list; hand-entered models lift it.
  const typedKeyUnverified = !!keyFetchError && !manualModels;

  // The stored credential listed nothing and no replacement key has been typed.
  // AWS is exempt: Bedrock has no list endpoint and no API Key field to explain
  // a block on, so gating there would lock those providers out of editing.
  const savedKeyUnverified =
    isEditMode &&
    !isAwsAuth &&
    hasFetched &&
    modelOptions.length === 0 &&
    !apiKey.trim() &&
    !manualModels;

  const allSelected =
    modelOptions.length > 0 && models.length === modelOptions.length;

  const preset = PROVIDER_PRESETS[name] || PROVIDER_PRESETS.custom;

  // A key FIELD_LABELS does not know goes last rather than dropping silently.
  const errorKeys = Object.keys(errors).filter((key) => errors[key]);
  const errorList = [
    ...Object.keys(FIELD_LABELS).filter((key) => errors[key]),
    ...errorKeys.filter((key) => !(key in FIELD_LABELS)),
  ].map((key) => ({
    key,
    label: FIELD_LABELS[key] || key,
    message: errors[key],
  }));

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>{isEditMode ? "Edit Provider" : "Add Provider"}</DialogTitle>
      <DialogContent ref={contentRef}>
        <Stack spacing={2} mt={1}>
          {errorList.length > 0 && !summaryDismissed && (
            <Alert severity="error" onClose={() => setSummaryDismissed(true)}>
              <AlertTitle>
                {errorList.length === 1
                  ? "Fix this before saving"
                  : `Fix ${errorList.length} issues before saving`}
              </AlertTitle>
              <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                {errorList.map(({ key, label, message }) => (
                  <li key={key}>
                    <Typography variant="body2" component="span">
                      <strong>{label}:</strong> {message}
                    </Typography>
                  </li>
                ))}
              </Box>
            </Alert>
          )}

          {/* Provider Name — dropdown for common providers */}
          {isEditMode ? (
            <TextField
              label="Provider Name"
              fullWidth
              required
              value={name}
              disabled
              error={!!errors.name}
              helperText={errors.name || "Provider name cannot be changed"}
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
                onChange={(e) => {
                  setBaseUrl(e.target.value);
                  setErrors((prev) => ({ ...prev, baseUrl: undefined }));
                }}
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
                  // The refetch is debounced; drop the stale verdict now.
                  setKeyFetchError("");
                }}
                placeholder={
                  isEditMode
                    ? "Leave blank to keep current key"
                    : preset.keyPlaceholder
                }
                error={
                  !!errors.apiKey || typedKeyUnverified || savedKeyUnverified
                }
                helperText={
                  errors.apiKey ||
                  keyFetchError ||
                  (savedKeyUnverified ? STORED_KEY_UNVERIFIED : undefined)
                }
              />
            </>
          )}

          {(() => {
            const currentPreset = PROVIDER_PRESETS[name];
            const visibleFormats = currentPreset?.supportedFormats?.length
              ? API_FORMATS.filter((f) =>
                  currentPreset.supportedFormats.includes(f),
                )
              : API_FORMATS;
            const isFiltered = visibleFormats.length < API_FORMATS.length;
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
                {modelsLoading && <CircularProgress size={14} sx={{ ml: 1 }} />}
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
              size="small"
              sx={{
                "&:hover .MuiAutocomplete-input, &.Mui-focused .MuiAutocomplete-input":
                  { minWidth: 30 },
              }}
              options={modelOptions}
              value={models}
              onChange={(_, val) => {
                setModels(normalizeModels(val));
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
                    modelsLoading
                      ? "Fetching models..."
                      : modelOptions.length > 0
                        ? "Select models..."
                        : isAwsAuth
                          ? "Type or paste comma-separated model IDs"
                          : "Enter API key to load models, or type manually"
                  }
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
            {models.length === 0 && (
              // Save is held until a model is picked, so say so here.
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ mt: 0.5, display: "block" }}
              >
                Select at least one model to enable Save
                {modelOptions.length === 0
                  ? " — type a model ID and press Enter to add one manually."
                  : "."}
              </Typography>
            )}
          </Box>

          <Stack direction="row" spacing={2}>
            <TextField
              label="Timeout"
              value={timeoutVal}
              onChange={(e) => {
                setTimeoutVal(e.target.value);
                setErrors((prev) => ({ ...prev, timeout: undefined }));
              }}
              placeholder="30s"
              error={!!errors.timeout}
              helperText={errors.timeout || "Seconds; accepts 30 or 30s"}
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
        {/* Held for the states a click could never get past. Each is explained
            on its own field, and each has a way out — pick or type a model, or
            enter a key that lists them. */}
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={
            !name.trim() ||
            updateProvider.isPending ||
            modelsLoading ||
            typedKeyUnverified ||
            savedKeyUnverified ||
            models.length === 0
          }
        >
          {modelsLoading
            ? "Loading models..."
            : updateProvider.isPending
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

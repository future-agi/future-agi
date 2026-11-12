import React, {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  useId,
} from "react";
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
  Link,
  ListSubheader,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useQueryClient } from "@tanstack/react-query";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import {
  gatewayConfigRefreshFailed,
  useUpdateProvider,
  useFetchProviderModels,
} from "./hooks/useGatewayConfig";
import {
  DEFAULT_API_PATH_PREFIX,
  SYSTEMONE_API_FORMAT,
  apiFormatLabel,
  getApiPathPrefix,
  isSystemOneFormat,
  normalizeApiFormat,
  parseTimeoutSeconds,
  withApiPathPrefix,
} from "./utils";
import {
  bedrockBaseUrl,
  bedrockRegionFromConfig,
  PROVIDER_GROUPS,
  PROVIDER_PRESETS,
} from "./byok-presets";
import { serverModelIds, droppedModelCount } from "./modelIdSafety";
import { PROVIDER_MODEL_DISCOVERY_ENABLED } from "./providerDiscovery";
import {
  DECISIONS_ONLY_HELP,
  DECISIONS_ONLY_TAG,
  MODEL_LIST_SOURCE_HELP,
  RESPONSES_ONLY_HELP,
  RESPONSES_ONLY_TAG,
  SYSTEM_ONE_MODELS_HELP,
  decisionModelsFor,
  defaultSelectableModels,
  responsesOnlyModelSet,
  withDecisionModels,
} from "./modelCompatibility";

// The built-in models of a System One preset, or none for any other provider.
// A copy, so state never aliases the preset.
const systemOneModelsFor = (providerName) => {
  const preset = PROVIDER_PRESETS[providerName];
  return isSystemOneFormat(preset?.apiFormat) ? [...preset.models] : [];
};

const parseProviderBaseUrl = (value) => {
  const url = value.trim();
  if (!/^https?:\/\//i.test(url) || /\s/.test(url)) return null;
  try {
    const parsed = new URL(url);
    return parsed.hostname ? parsed : null;
  } catch {
    return null;
  }
};

// Mirror the gateway's vendor recognition: a custom label cannot turn a
// TypeSafe, Perplexity or OpenAI endpoint into a keyless self-hosted provider.
const allowsKeylessSystemOne = (name, apiFormat, baseUrl) => {
  if (!isSystemOneFormat(apiFormat)) return false;
  const url = parseProviderBaseUrl(baseUrl);
  if (!url || /^(typesafe|perplexity|openai)/i.test(name)) return false;
  const host = url.hostname.toLowerCase().replace(/\.$/, "");
  return !["typesafe.ai", "perplexity.ai", "openai.com"].some(
    (vendor) => host === vendor || host.endsWith(`.${vendor}`),
  );
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

// A custom provider's name is its ID: model IDs are prefixed with it, keys
// allow it by name, and saving under a name that exists replaces that provider.
const customNameError = (value, existingNames) => {
  if (!value) return "Name is required";
  if (!/^[a-z0-9][a-z0-9_-]{0,62}$/.test(value)) {
    return "Use lowercase letters, digits, - and _, starting with a letter or digit";
  }
  if (value !== "custom" && Object.hasOwn(PROVIDER_PRESETS, value)) {
    return `"${value}" is a built-in provider. Choose it from the Provider list, or use another name`;
  }
  if (existingNames.includes(value)) {
    return `A provider named "${value}" already exists. Use another name, or edit that provider`;
  }
  return "";
};

const API_FORMATS = [
  "openai",
  "anthropic",
  "cohere",
  "google",
  "gemini",
  "azure",
  "bedrock",
  SYSTEMONE_API_FORMAT,
];

const vertexSettingsFromUrl = (url) => {
  const match = String(url || "").match(
    /^https:\/\/(?:[a-z0-9-]+-)?aiplatform\.googleapis\.com\/v1beta1\/projects\/([^/]+)\/locations\/([^/]+)\/?$/,
  );
  return { project: match?.[1] || "", location: match?.[2] || "us-central1" };
};

// The API types a provider's models as free-form JSON, so an entry need not be
// a string — and a non-string one takes the dialog down when it reaches state.
const toModelId = (value) => {
  if (typeof value === "string") return value.trim();
  if (value && typeof value === "object") {
    return String(value.id ?? value.name ?? value.model ?? "").trim();
  }
  return value == null ? "" : String(value).trim();
};

// Manual input is explicit user text; discovered and saved lists use only the
// server-admitted response via serverModelIds. No second admission regex here.
const normalizeModels = (list) =>
  Array.isArray(list) ? list.map(toModelId).filter(Boolean) : [];

// A message as a sentence: it ends with a full stop unless it already ends
// with one, a question mark or an exclamation mark.
const asSentence = (text) => {
  const trimmed = String(text || "").trim();
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
};

const KEY_FETCH_FAILED =
  "Couldn't load any models with this key. Check that it is valid for this " +
  "provider, or add model IDs manually below.";

const SAVED_BASE_URL_MODEL_NOTE =
  "Model loading uses the saved Base URL. Save the URL change, then reopen this dialog to load models, or enter an API key to test the edited URL before saving.";

// Google-name discovery ignores the configured URL in its compatibility mode.
// The native Anthropic branch takes precedence over the Google-name branch.
const listsAtDefaultGoogleUrl = (providerName, format) =>
  ["google", "gemini", "google_gemini"].includes(providerName.toLowerCase()) &&
  !["anthropic", "gemini", "google"].includes(format);

// Summary labels, in form order so the summary reads like the dialog.
const FIELD_LABELS = {
  name: "Provider Name",
  customName: "Name",
  awsRegion: "AWS region",
  awsAccessKeyId: "AWS Access Key ID",
  awsSecretAccessKey: "AWS Secret Access Key",
  baseUrl: "Base URL",
  apiKey: "API Key",
  timeout: "Timeout",
};

// Names the endpoint a model is served on. It rides with the ID so the warning
// follows the model from the dropdown into the selected chip.
const EndpointTag = ({ label }) => (
  <Typography
    component="span"
    variant="caption"
    sx={{
      ml: 0.75,
      px: 0.5,
      borderRadius: 0.5,
      bgcolor: "warning.lighter",
      color: "warning.darker",
      whiteSpace: "nowrap",
    }}
  >
    {label}
  </Typography>
);

EndpointTag.propTypes = {
  label: PropTypes.string.isRequired,
};

// Under the provider picker: the key used for requests and, for a named
// vendor, a link to its API docs.
const PresetNote = ({ preset }) => {
  const vendor = preset.source === "custom" ? null : preset.label;
  return (
    <>
      {vendor
        ? `Requests use your own ${vendor} key.`
        : "Requests use your own provider key."}
      {preset.docsUrl && (
        <>
          {" "}
          <Link
            href={preset.docsUrl}
            target="_blank"
            rel="noopener noreferrer"
            underline="hover"
          >
            {vendor} docs
          </Link>
        </>
      )}
    </>
  );
};

PresetNote.propTypes = {
  preset: PropTypes.shape({
    label: PropTypes.string,
    source: PropTypes.string,
    docsUrl: PropTypes.string,
  }).isRequired,
};

const EMPTY_PROVIDERS = Object.freeze({});

const AddProviderDialog = ({
  open,
  onClose,
  gatewayId,
  provider: editTarget,
  existingNames = [],
  existingProviders = EMPTY_PROVIDERS,
}) => {
  const [name, setName] = useState("openai");
  // Keep the picker available when arriving from Add, but hydrate the actual
  // connection and label its action as an edit. No write occurs on selection.
  const provider = useMemo(
    () => editTarget || (
      name !== "custom" &&
      Object.hasOwn(existingProviders, name) &&
      existingProviders[name]
        ? { name, config: existingProviders[name] }
        : null
    ),
    [editTarget, existingProviders, name],
  );
  const isEditMode = Boolean(provider);
  const connectedNames = Array.from(
    new Set([...existingNames, ...Object.keys(existingProviders)]),
  );
  const missingConnectedConfig =
    !isEditMode && name !== "custom" && connectedNames.includes(name);
  const [modelInput, setModelInput] = useState("");
  // The saved name of a custom provider; `name` holds the preset key.
  const [customName, setCustomName] = useState("custom");
  const [baseUrl, setBaseUrl] = useState(PROVIDER_PRESETS.openai.baseUrl);
  const [apiKey, setApiKey] = useState("");
  const [apiFormat, setApiFormat] = useState("openai");
  const [apiPathPrefix, setApiPathPrefix] = useState(DEFAULT_API_PATH_PREFIX);
  const [models, setModels] = useState([]);
  const [timeoutVal, setTimeoutVal] = useState("");
  const [maxConcurrent, setMaxConcurrent] = useState("");
  const [advancedExpanded, setAdvancedExpanded] = useState(false);
  const [advancedFocus, setAdvancedFocus] = useState(null);
  const advancedInputRefs = useRef({});
  const advancedId = useId();

  // Focus only after the hidden field has become visible in the committed DOM.
  useEffect(() => {
    if (!open || !advancedExpanded || !advancedFocus) return;
    const input = advancedInputRefs.current[advancedFocus];
    input?.focus();
    input?.scrollIntoView?.({ block: "nearest" });
    setAdvancedFocus(null);
  }, [open, advancedExpanded, advancedFocus]);

  // AWS Bedrock credentials
  const [awsAccessKeyId, setAwsAccessKeyId] = useState("");
  const [awsSecretAccessKey, setAwsSecretAccessKey] = useState("");
  const [awsRegion, setAwsRegion] = useState("us-east-1");
  const [awsSessionToken, setAwsSessionToken] = useState("");
  const [gcpProject, setGcpProject] = useState("");
  const [gcpLocation, setGcpLocation] = useState("us-central1");
  const [serviceAccountJSON, setServiceAccountJSON] = useState("");

  // Validation state
  const [errors, setErrors] = useState({});
  const [summaryDismissed, setSummaryDismissed] = useState(false);
  // Every model ID a listing has offered in this dialog, plus the ones already
  // stored on the provider. Anything selected outside it was typed by hand.
  const offeredModels = useRef(new Set());
  // The body scrolls, so a failed Save has to bring the summary back into view.
  const contentRef = useRef(null);

  const queryClient = useQueryClient();
  const updateProvider = useUpdateProvider();
  const fetchModels = useFetchProviderModels();
  const projectSavedModels = useFetchProviderModels();
  const hydrationSeqRef = useRef(0);
  const [savedModelsPending, setSavedModelsPending] = useState(false);
  const [savedModelsFailed, setSavedModelsFailed] = useState(false);
  const [modelOptions, setModelOptions] = useState([]);
  const [fetchError, setFetchError] = useState("");
  const [currentOmittedCount, setCurrentOmittedCount] = useState(0);
  const [savedOmittedCount, setSavedOmittedCount] = useState(0);
  const [savedLoadedCount, setSavedLoadedCount] = useState(0);
  const omittedModelCount = currentOmittedCount + savedOmittedCount;
  const [saveOmittedCount, setSaveOmittedCount] = useState(0);
  const savedWriteOmittedCount =
    droppedModelCount(updateProvider.error?.response?.dropped_model_count) ||
    saveOmittedCount;
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

  // The prefix the stored-credential listing was fetched with, so editing the
  // field is told apart from the dialog hydrating it.
  const byNameFetchPrefix = useRef(null);

  const doFetchModels = useCallback(
    ({ providerName, url, key, format, prefix }) => {
      // Every discovery path, including edits to a saved path prefix, is inert
      // for a decision wire format: discovery must never make a billed call.
      if (!PROVIDER_MODEL_DISCOVERY_ENABLED || isSystemOneFormat(format)) return;
      const seq = fetchSeqRef.current + 1;
      fetchSeqRef.current = seq;
      const usingSavedKey = Boolean(providerName) && !key;
      if (!usingSavedKey) typedFetchSeqRef.current = seq;
      else byNameFetchPrefix.current = prefix;
      const isStale = () => fetchSeqRef.current !== seq;
      setCurrentOmittedCount(0);
      setFetchError("");
      setKeyFetchError("");
      setHasFetched(false);
      // A by-name fetch uses the stored credential; no key here to blame.
      const blameKey = !usingSavedKey;
      fetchModels.mutate(
        {
          ...(providerName ? { providerName } : {}),
          ...(url !== undefined ? { baseUrl: url } : {}),
          ...(key !== undefined ? { apiKey: key } : {}),
          apiFormat: format,
          apiPathPrefix: prefix,
        },
        {
          onSuccess: (result) => {
            if (isStale()) return;
            const authRejected =
              result?.code === "provider_authentication_failed";
            const fetched = authRejected ? [] : serverModelIds(result?.models);
            const omitted = droppedModelCount(result?.dropped_model_count);
            setCurrentOmittedCount(omitted);
            // A typed authentication refusal wins over every omission count.
            const emptyReason = authRejected
              ? KEY_FETCH_FAILED
              : fetched.length === 0 && omitted === 0
                ? result?.error || "Provider returned no models"
                : "";
            setFetchError(emptyReason);
            if (emptyReason && blameKey) setKeyFetchError(KEY_FETCH_FAILED);
            fetched.forEach((m) => offeredModels.current.add(m));
            setModelOptions(fetched);
            const hasVerdict =
              authRejected || fetched.length > 0 || omitted === 0;
            setHasFetched(hasVerdict);
            if (usingSavedKey) {
              storedKeyResult.current = {
                options: fetched,
                error: emptyReason,
                omitted,
                hasFetched: hasVerdict,
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
            if (usingSavedKey) {
              storedKeyResult.current = {
                options: [],
                error: message,
                omitted: 0,
                hasFetched: false,
              };
            }
          },
        },
      );
    },
    [fetchModels],
  );

  const loadSavedModels = (providerName) => {
    setSavedModelsPending(true);
    setSavedModelsFailed(false);
    const hydrationSeq = ++hydrationSeqRef.current;
    const fail = () => {
      if (hydrationSeq !== hydrationSeqRef.current) return;
      setSavedModelsFailed(true);
      setSavedModelsPending(false);
    };
    projectSavedModels.mutate(
      { providerName, savedModelsOnly: true },
      {
        onSuccess: (result) => {
          if (hydrationSeq !== hydrationSeqRef.current) return;
          if (!Array.isArray(result?.models)) { fail(); return; }
          const admitted = serverModelIds(result.models);
          setModels((current) => Array.from(new Set([...current, ...admitted])));
          admitted.forEach((m) => offeredModels.current.add(m));
          setSavedOmittedCount(droppedModelCount(result?.dropped_model_count));
          setSavedLoadedCount(admitted.length);
          setSavedModelsPending(false);
        },
        onError: fail,
      },
    );
  };

  // Edit mode: populate form from existing provider
  useEffect(() => {
    if (open && isEditMode && provider) {
      const c = provider.config || {};
      // A different connection must not inherit drafts or asynchronous results
      // from the previous one. Saved credentials are never put into inputs.
      fetchSeqRef.current += 1;
      setModelInput("");
      setModelOptions([]);
      setFetchError("");
      setKeyFetchError("");
      setHasFetched(false);
      setFetchScheduled(false);
      setSaveOmittedCount(0);
      setAwsAccessKeyId("");
      setAwsSecretAccessKey("");
      setAwsSessionToken("");
      setAwsRegion(
        PROVIDER_PRESETS[provider.name]?.authType === "aws"
          ? bedrockRegionFromConfig(c)
          : "us-east-1",
      );
      setName(provider.name || "");
      setBaseUrl(c.base_url ?? c.baseUrl ?? "");
      setApiKey("");
      // "typesafe" is the gateway's alias for System One; the select only
      // offers the canonical name. A decision preset's name only ever means
      // System One, so a credential pushed under it with another format (the
      // admin API defaults to openai) is shown, and saved, as System One rather
      // than with a blank, locked format.
      const storedFormat = isSystemOneFormat(
        PROVIDER_PRESETS[provider.name]?.apiFormat,
      )
        ? SYSTEMONE_API_FORMAT
        : provider.name === "bedrock"
          ? "bedrock" // Legacy dialogs could save Anthropic here; Bedrock needs SigV4.
          : normalizeApiFormat(c.api_format ?? c.apiFormat ?? "openai");
      setApiFormat(storedFormat);
      const storedPrefix = getApiPathPrefix(c);
      setApiPathPrefix(storedPrefix);
      // Legacy gateway reads are not an admission authority. Hydrate from the
      // organization-scoped server projection, without probing the provider.
      setModels([]);
      offeredModels.current = new Set();
      setSavedOmittedCount(0);
      setSavedLoadedCount(0);
      setCurrentOmittedCount(0);
      loadSavedModels(provider.name);
      const timeoutRaw = c.default_timeout ?? c.defaultTimeout;
      setTimeoutVal(timeoutRaw != null ? String(timeoutRaw) : "");
      setMaxConcurrent(String(c.max_concurrent ?? c.maxConcurrent ?? ""));
      const vertexSettings = vertexSettingsFromUrl(c.base_url ?? c.baseUrl);
      setGcpProject(vertexSettings.project);
      setGcpLocation(vertexSettings.location);
      setServiceAccountJSON("");
      setErrors({});
      setSummaryDismissed(false);
      setAdvancedExpanded(false);
      setAdvancedFocus(null);
      storedKeyResult.current = null;
      byNameFetchPrefix.current = null;
      if (isSystemOneFormat(storedFormat)) {
        // Never probed; the preset's list stands in for a listing.
        const builtIn = systemOneModelsFor(provider.name);
        builtIn.forEach((m) => offeredModels.current.add(m));
        setModelOptions(builtIn);
        setFetchError("");
        setKeyFetchError("");
        setHasFetched(false);
      } else if (provider.name !== "vertex") {
        doFetchModels({
          providerName: provider.name,
          format: storedFormat,
          prefix: storedPrefix,
        });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, isEditMode, provider]);

  // Reset on close as well as an initial Add. Switching presets resets in its
  // handler, not an isEditMode effect that would put the picker back on OpenAI.
  useEffect(() => {
    if (!open || !isEditMode) resetForm();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const isAwsAuth = PROVIDER_PRESETS[name]?.authType === "aws";
  const isVertexAuth = PROVIDER_PRESETS[name]?.authType === "gcp";
  const isSystemOne = isSystemOneFormat(apiFormat);
  const keyIsOptional = allowsKeylessSystemOne(name, apiFormat, baseUrl);

  // Auto-fetch when an API key is entered (debounced). Edit mode included: a
  // typed key replaces the stored one, so it has to list models before Save.
  // Skip auto-fetch for AWS providers (Bedrock models must be entered manually)
  useEffect(() => {
    if (!PROVIDER_MODEL_DISCOVERY_ENABLED || isAwsAuth || isVertexAuth) {
      setFetchScheduled(false);
      return;
    }
    // System One providers are never probed, so a key cannot start a billed
    // call from this dialog. Their model list is the preset's, set when the
    // preset is picked or the dialog opens on a saved one.
    if (isSystemOne) {
      fetchSeqRef.current += 1;
      setFetchScheduled(false);
      setFetchError("");
      setKeyFetchError("");
      setHasFetched(false);
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
          setCurrentOmittedCount(snapshot.omitted || 0);
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
      doFetchModels({
        providerName: isEditMode ? provider?.name : undefined,
        url: baseUrl,
        key: apiKey.trim(),
        format: apiFormat,
        prefix: apiPathPrefix,
      });
    }, 600);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    apiKey,
    baseUrl,
    apiFormat,
    apiPathPrefix,
    isEditMode,
    isAwsAuth,
    isVertexAuth,
    isSystemOne,
  ]);

  // Editing the prefix moves the discovery URL, so the stored-credential
  // listing has to be fetched again. The effect above cannot do it: a blank key
  // in edit mode means "keep the stored one", which returns before any fetch.
  useEffect(() => {
    if (!PROVIDER_MODEL_DISCOVERY_ENABLED || !open || !isEditMode || !provider) return;
    if (apiKey.trim()) return;
    if (isAwsAuth || isVertexAuth || isSystemOne || provider.name === "vertex")
      return;
    // null until the hydrating fetch records one, so opening the dialog is not
    // mistaken for a change.
    if (byNameFetchPrefix.current === null) return;
    if (byNameFetchPrefix.current === apiPathPrefix) return;

    const timer = setTimeout(() => {
      doFetchModels({
        providerName: provider.name,
        format: apiFormat,
        prefix: apiPathPrefix,
      });
    }, 600);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    apiPathPrefix,
    apiKey,
    apiFormat,
    open,
    isEditMode,
    isAwsAuth,
    isVertexAuth,
    isSystemOne,
  ]);

  const resetForm = () => {
    fetchSeqRef.current += 1;
    setModelInput("");
    const defaultPreset = PROVIDER_PRESETS.openai;
    setName("openai");
    setCustomName("custom");
    setBaseUrl(defaultPreset.baseUrl);
    setApiKey("");
    setApiFormat(defaultPreset.apiFormat);
    setApiPathPrefix(DEFAULT_API_PATH_PREFIX);
    setModels([]);
    setModelOptions([]);
    hydrationSeqRef.current += 1;
    setSavedModelsPending(false);
    setSavedModelsFailed(false);
    setSavedOmittedCount(0);
    setSavedLoadedCount(0);
    setCurrentOmittedCount(0);
    setSaveOmittedCount(0);
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
    setGcpProject("");
    setGcpLocation("us-central1");
    setServiceAccountJSON("");
    setErrors({});
    setSummaryDismissed(false);
    setAdvancedExpanded(false);
    setAdvancedFocus(null);
    offeredModels.current = new Set();
    storedKeyResult.current = null;
    byNameFetchPrefix.current = null;
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  const handleProviderChange = (newName) => {
    resetForm();
    setName(newName);
    const preset = PROVIDER_PRESETS[newName];
    if (preset) {
      setBaseUrl(
        preset.authType === "aws" ? bedrockBaseUrl("us-east-1") : preset.baseUrl,
      );
      // Format and endpoint are one preset, not independent defaults.
      setApiFormat(preset.apiFormat);
    }
    setApiPathPrefix(preset?.apiPathPrefix ?? DEFAULT_API_PATH_PREFIX);
    // System One brings its static list; it is never probed. A connected
    // preset is subsequently hydrated by the edit effect instead.
    const builtIn = systemOneModelsFor(newName);
    setModels(builtIn);
    setModelOptions(builtIn);
    offeredModels.current = new Set(builtIn);
  };

  const handleApiFormatChange = (format) => {
    setApiFormat(format);
    const preset = PROVIDER_PRESETS[name];
    // Only an explicit format change selects a new endpoint tuple. Hydrating
    // an existing connection must preserve its saved URL and prefix.
    if (preset?.formatOverrides) {
      const endpoint = preset.formatOverrides[format] || preset;
      setBaseUrl(endpoint.baseUrl);
      setApiPathPrefix(endpoint.apiPathPrefix ?? DEFAULT_API_PATH_PREFIX);
    }
  };

  const handleAwsRegionChange = (region) => {
    setAwsRegion(region);
    setBaseUrl(bedrockBaseUrl(region));
    setErrors((prev) => ({ ...prev, awsRegion: undefined }));
  };

  const addManualModels = (text) => {
    const tokens = text.split(/[,\r\n]/).map((id) => id.trim()).filter(Boolean);
    if (!tokens.length) return;
    setModels((prev) => Array.from(new Set([...prev, ...tokens])));
    setModelInput("");
  };

  // What the model rules below match on. The format rides along so the
  // Perplexity Decider, on Perplexity's host, is not read as its chat API.
  const modelContext = useMemo(
    () => ({ providerName: name, baseUrl, apiFormat }),
    [name, baseUrl, apiFormat],
  );

  // Which listed models this provider serves on /v1/responses rather than chat
  // completions. Built from the live catalogue only: `models` can hold an ID
  // the user typed by hand (e.g. Perplexity's `sonar`, which chat completions
  // serves and the catalogue never lists), and that must never be tagged.
  const responsesOnly = useMemo(
    () => responsesOnlyModelSet(modelOptions, modelContext),
    [modelOptions, modelContext],
  );

  // Decision models this chat provider also serves (OpenAI's gpt-6-luna). The
  // ID means the same model however it got here, so a typed one is tagged too.
  const decisionsOnly = useMemo(
    () => new Set(decisionModelsFor(modelContext)),
    [modelContext],
  );

  // The live listing plus any of those it left out.
  const pickerOptions = useMemo(
    () => withDecisionModels(modelOptions, modelContext),
    [modelOptions, modelContext],
  );

  // A built-in option counts as offered, like a listed one: picking it is not
  // a hand-typed override of a listing that later comes back empty.
  useEffect(() => {
    pickerOptions.forEach((m) => offeredModels.current.add(m));
  }, [pickerOptions]);

  // What "Select All" covers. Tagged models stay out, which is what keeps them
  // unselected by default; they remain individually selectable.
  const bulkSelectable = useMemo(
    () => defaultSelectableModels(pickerOptions, modelContext),
    [pickerOptions, modelContext],
  );

  const allSelected =
    bulkSelectable.length > 0 &&
    bulkSelectable.every((m) => models.includes(m));

  const handleSelectAll = () => {
    if (allSelected) {
      setModels([]);
    } else {
      // Union, not replacement: a tagged model the user picked on purpose, or
      // one restored from the saved config, must survive this.
      setModels((prev) => Array.from(new Set([...prev, ...bulkSelectable])));
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
    if (!isEditMode && name === "custom") {
      const nameError = customNameError(customName.trim(), connectedNames);
      if (nameError) newErrors.customName = nameError;
    }

    if (isVertexAuth) {
      if (!/^[a-z][a-z0-9-]{4,62}$/.test(gcpProject.trim())) {
        newErrors.gcpProject = "Enter a valid Google Cloud project ID";
      }
      if (!/^[a-z][a-z0-9-]{1,62}$/.test(gcpLocation.trim())) {
        newErrors.gcpLocation = "Enter a valid Vertex AI location";
      }
      if (!isEditMode && !serviceAccountJSON.trim()) {
        newErrors.serviceAccountJSON = "Paste the Google service-account JSON";
      } else if (serviceAccountJSON.trim()) {
        try {
          const parsed = JSON.parse(serviceAccountJSON);
          if (
            parsed?.type !== "service_account" ||
            !parsed.client_email ||
            !parsed.private_key ||
            !parsed.project_id ||
            parsed.token_uri !== "https://oauth2.googleapis.com/token"
          ) {
            newErrors.serviceAccountJSON =
              "Paste a complete Google service-account JSON key";
          }
        } catch {
          newErrors.serviceAccountJSON = "Paste valid JSON";
        }
      }
    } else if (isAwsAuth) {
      if (!awsRegion) newErrors.awsRegion = "Choose the AWS region before saving";
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
      } else if (baseUrl.trim() && !parseProviderBaseUrl(baseUrl)) {
        newErrors.baseUrl =
          "Enter a valid http(s) base URL with a host and valid port";
      }

      // Known vendors require a key; only valid self-hosted System One may omit it.
      if (!isEditMode && !keyIsOptional && !apiKey.trim()) {
        newErrors.apiKey = "API key is required";
      }

      // Only a listing that came back empty counts, and only until models are
      // entered by hand.
      if (
        !isSystemOne &&
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
    const advancedError = newErrors.baseUrl && optionalBaseUrl
      ? "baseUrl"
      : newErrors.timeout ? "timeout" : null;
    if (advancedError) {
      setAdvancedExpanded(true);
      setAdvancedFocus(advancedError);
    }
    return Object.keys(newErrors).length === 0;
  };

  const handleSave = () => {
    if (missingConnectedConfig || savedModelsPending || savedModelsFailed) return;
    const timeoutSeconds = parseTimeoutSeconds(timeoutVal);
    if (!validate(timeoutSeconds)) {
      // scrollTo is missing in jsdom — never throw on a cosmetic scroll.
      contentRef.current?.scrollTo?.({ top: 0, behavior: "smooth" });
      return;
    }

    const config = withApiPathPrefix(
      { base_url: baseUrl.trim(), api_format: apiFormat },
      apiFormat,
      apiPathPrefix,
    );
    if (isVertexAuth) {
      config.gcp_project = gcpProject.trim();
      config.gcp_location = gcpLocation.trim();
      if (serviceAccountJSON.trim())
        config.service_account_json = serviceAccountJSON;
    } else if (isAwsAuth) {
      if (awsAccessKeyId.trim()) config.aws_access_key_id = awsAccessKeyId;
      if (awsSecretAccessKey.trim())
        config.aws_secret_access_key = awsSecretAccessKey;
      if (awsRegion) config.aws_region = awsRegion;
      if (awsSessionToken.trim()) config.aws_session_token = awsSessionToken;
    } else if (apiKey.trim()) {
      config.api_key = apiKey.trim();
    }
    // The endpoint replaces these metadata fields even when omitted. Preserve
    // only the represented non-secret settings, never arbitrary saved config.
    if (isEditMode) {
      const saved = provider.config || {};
      if (saved.display_name != null) config.display_name = saved.display_name;
      if (saved.conn_pool_size != null) config.conn_pool_size = saved.conn_pool_size;
    }
    if (models.length > 0) config.models = models;
    if (timeoutSeconds !== null) config.default_timeout = timeoutSeconds;
    if (maxConcurrent) config.max_concurrent = Number(maxConcurrent);

    const providerName =
      !isEditMode && name === "custom" ? customName.trim() : name;
    updateProvider.mutate(
      { gatewayId, name: providerName, config },
      {
        // A save the gateway did not apply never reaches onSuccess:
        // useUpdateProvider reports it once, as a warning, and the dialog
        // stays open with the same sentence in its Alert.
        onSuccess: (result) => {
          const dropped = droppedModelCount(result?.dropped_model_count);
          setSaveOmittedCount(dropped);
          // The write landed, and the re-read that follows can still have
          // failed: a stale screen, not a lost save. Say which.
          const saved = isEditMode
            ? `Provider "${providerName}" updated`
            : `Provider "${providerName}" added`;
          const omissions = dropped
            ? ` ${dropped} model IDs were omitted for safety.`
            : "";
          if (gatewayConfigRefreshFailed(queryClient)) {
            enqueueSnackbar(
              `${saved}, but the provider list could not be reloaded — refresh the page to see it.${omissions}`,
              { variant: "warning" },
            );
          } else {
            enqueueSnackbar(dropped ? `${saved}.${omissions}` : saved, {
              variant: dropped ? "warning" : "success",
            });
          }
          // A partial model save needs an explicit visible notice, not an
          // unqualified success followed by a closed dialog.
          if (!dropped) handleClose();
        },
        onError: (err) => {
          enqueueSnackbar(
            getSafeActionErrorMessage(
              err,
              isEditMode
                ? "Failed to update provider"
                : "Failed to add provider",
            ),
            { variant: "error" },
          );
        },
      },
    );
  };

  const modelsLoading =
    savedModelsPending ||
    (PROVIDER_MODEL_DISCOVERY_ENABLED &&
      !isSystemOne && (fetchScheduled || fetchModels.isPending));

  // A typed key answered with an empty list; hand-entered models lift it.
  const typedKeyUnverified = !isSystemOne && !!keyFetchError && !manualModels;

  // Every saved model is held back (none could be loaded), so an edit may
  // save without models: the request leaves the list out and the server keeps
  // the saved IDs.
  const keepsHeldBackModels =
    isEditMode && savedOmittedCount > 0 && savedLoadedCount === 0;

  // The stored credential's model list failed or came back empty, and no
  // replacement key has been typed: that is no verdict on the key (the
  // provider's upstream may be down), so Save keeps the provider's current
  // models and the failure reads as a warning beside them.
  const storedListFailed =
    isEditMode &&
    !isAwsAuth &&
    !isVertexAuth &&
    !isSystemOne &&
    Boolean(fetchError) &&
    modelOptions.length === 0 &&
    !apiKey.trim() &&
    models.length > 0;

  const savedBaseUrl =
    provider?.config?.base_url ?? provider?.config?.baseUrl ?? "";
  const savedApiFormat =
    provider?.config?.api_format ?? provider?.config?.apiFormat ?? "openai";
  // Keep this guidance limited to the exactly empty key field.
  // Trimming here compares URLs; it never changes a fetch or save payload.
  const showSavedBaseUrlNote =
    PROVIDER_MODEL_DISCOVERY_ENABLED &&
    isEditMode &&
    !isAwsAuth &&
    !isVertexAuth &&
    !isSystemOne &&
    !isSystemOneFormat(savedApiFormat) &&
    apiKey === "" &&
    Boolean(savedBaseUrl.trim()) &&
    Boolean(baseUrl.trim()) &&
    baseUrl.trim() !== savedBaseUrl.trim() &&
    !listsAtDefaultGoogleUrl(name, savedApiFormat) &&
    !listsAtDefaultGoogleUrl(name, apiFormat);

  const preset = PROVIDER_PRESETS[name] || PROVIDER_PRESETS.custom;
  const optionalBaseUrl = !isAwsAuth && !isVertexAuth && Boolean(preset.baseUrl);
  const presetEndpoint = preset.formatOverrides?.[apiFormat] || preset;
  const customBaseUrl = optionalBaseUrl && Boolean(baseUrl.trim()) &&
    baseUrl.trim() !== presetEndpoint.baseUrl;
  const customConnection = customBaseUrl || apiFormat !== preset.apiFormat ||
    (apiFormat === "openai" && apiPathPrefix !== (presetEndpoint.apiPathPrefix ?? DEFAULT_API_PATH_PREFIX)) ||
    Boolean(timeoutVal.trim()) || Boolean(maxConcurrent);
  const timeoutSeconds = parseTimeoutSeconds(timeoutVal);
  // Never invent numeric gateway defaults or repeat a possibly credentialed URL
  // in the summary. Draft overrides remain in state when the section is hidden.
  const connectionSummary = [
    customConnection ? "Custom settings" : "Default settings",
    ...(optionalBaseUrl ? [`Base URL: ${customBaseUrl ? "custom" : baseUrl.trim() ? "preset" : "gateway default"}`] : []),
    `API format: ${apiFormatLabel(apiFormat)}`,
    ...(apiFormat === "openai" ? [`Path prefix: ${apiPathPrefix || "none"}`] : []),
    `Timeout: ${timeoutVal.trim() ? timeoutSeconds === null ? "needs correction" : `${timeoutSeconds}s` : "gateway default"}`,
    `Concurrency: ${maxConcurrent || "gateway default"}`,
  ].join(" · ");

  const baseUrlField = (
    <TextField
      label="Base URL"
      fullWidth
      required={!preset.baseUrl}
      inputRef={(input) => { advancedInputRefs.current.baseUrl = input; }}
      value={baseUrl}
      onChange={(e) => {
        setBaseUrl(e.target.value);
        setErrors((prev) => ({ ...prev, baseUrl: undefined }));
      }}
      placeholder={preset.baseUrl || "https://your-endpoint.com"}
      error={!!errors.baseUrl}
      helperText={
        errors.baseUrl ||
        preset.baseUrlHelp ||
        (preset.baseUrl
          ? "Auto-filled from provider preset"
          : baseUrl.trim()
            ? undefined
            : "Required — enter your endpoint URL")
      }
    />
  );

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
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle>
        {isEditMode
          ? (editTarget ? "Edit provider" : "Edit existing provider")
          : "Add provider"}
      </DialogTitle>
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

          {!editTarget && (isEditMode || missingConnectedConfig) && (
            <Alert severity="info">
              {isEditMode
                ? "Already connected. You are editing the saved connection. Nothing changes until you save; leave credentials blank to keep them."
                : "Already connected. Close this dialog and edit the existing provider from the list, or choose another provider."}
            </Alert>
          )}

          {/* Direct edits lock the name; Add keeps the picker to choose another provider. */}
          {editTarget ? (
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
              helperText={errors.name || <PresetNote preset={preset} />}
              SelectProps={{
                MenuProps: { PaperProps: { sx: { maxHeight: 420 } } },
              }}
            >
              {PROVIDER_GROUPS.flatMap((group) => [
                <ListSubheader key={`group-${group.label}`}>
                  {group.label}
                </ListSubheader>,
                ...group.ids.map((id) => (
                  <MenuItem key={id} value={id}>
                    {PROVIDER_PRESETS[id].label}
                    {id !== "custom" && connectedNames.includes(id) && (
                      <Typography
                        component="span"
                        variant="caption"
                        color="text.secondary"
                        sx={{ ml: 1 }}
                      >
                        Already connected
                      </Typography>
                    )}
                  </MenuItem>
                )),
              ])}
            </TextField>
          )}

          {!isEditMode && name === "custom" && (
            <TextField
              label="Name"
              fullWidth
              required
              value={customName}
              onChange={(e) => {
                setCustomName(e.target.value);
                setErrors((prev) => ({ ...prev, customName: undefined }));
              }}
              error={!!errors.customName}
              helperText={
                errors.customName ||
                "Model IDs and key permissions use this name. It cannot be changed later."
              }
            />
          )}

          {isVertexAuth ? (
            <>
              <TextField
                label="Google Cloud project ID"
                fullWidth
                required
                value={gcpProject}
                onChange={(e) => {
                  setGcpProject(e.target.value);
                  setErrors((prev) => ({ ...prev, gcpProject: undefined }));
                }}
                error={!!errors.gcpProject}
                helperText={errors.gcpProject}
              />
              <TextField
                label="Vertex AI location"
                fullWidth
                required
                value={gcpLocation}
                onChange={(e) => {
                  setGcpLocation(e.target.value);
                  setErrors((prev) => ({ ...prev, gcpLocation: undefined }));
                }}
                error={!!errors.gcpLocation}
                helperText={errors.gcpLocation || "For example, us-central1"}
              />
              <TextField
                label="Service-account JSON"
                fullWidth
                required={!isEditMode}
                type="password"
                autoComplete="off"
                value={serviceAccountJSON}
                onChange={(e) => {
                  setServiceAccountJSON(e.target.value);
                  setErrors((prev) => ({
                    ...prev,
                    serviceAccountJSON: undefined,
                  }));
                }}
                placeholder={
                  isEditMode
                    ? "Leave blank to keep current key"
                    : "Paste the entire JSON key"
                }
                error={!!errors.serviceAccountJSON}
                helperText={
                  errors.serviceAccountJSON ||
                  "Encrypted at rest. Never returned to the browser after saving."
                }
              />
            </>
          ) : isAwsAuth ? (
            <>
              <TextField
                label="AWS Region"
                select
                fullWidth
                required
                value={awsRegion}
                onChange={(e) => handleAwsRegionChange(e.target.value)}
                error={!awsRegion || !!errors.awsRegion}
                helperText={
                  errors.awsRegion || (!awsRegion
                    ? "Saved region could not be determined. Choose a region before saving; this will update the Bedrock endpoint."
                    : "Changing the region also changes the Bedrock endpoint.")
                }
              >
                {!awsRegion && <MenuItem value="" disabled>Choose a region</MenuItem>}
                {awsRegion && !AWS_REGIONS.some((r) => r.value === awsRegion) && (
                  <MenuItem value={awsRegion}>{awsRegion}</MenuItem>
                )}
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
                helperText={
                  isEditMode
                    ? "Leave all credential fields blank to keep the saved credentials. Replacing both AWS keys with a blank session token removes the stored token. For temporary credentials (STS), enter the new token too."
                    : "Only needed for temporary AWS credentials (STS)"
                }
              />
            </>
          ) : (
            <>
              {!optionalBaseUrl && baseUrlField}

              <TextField
                label="API Key"
                fullWidth
                required={!isEditMode && !keyIsOptional}
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
                error={!!errors.apiKey || typedKeyUnverified}
                helperText={
                  errors.apiKey ||
                  keyFetchError ||
                  (keyIsOptional
                    ? "Optional for a self-hosted System One endpoint that does not require authentication. Known vendors still require an API key."
                    : undefined)
                }
              />
            </>
          )}

          <Box>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              mb={0.5}
            >
              <Typography variant="subtitle2" color="text.secondary">
                Models
                <Typography component="span" variant="caption" role="status" sx={{ ml: 1 }}>
                  {models.length} selected
                </Typography>
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

            {!PROVIDER_MODEL_DISCOVERY_ENABLED && (
              <Alert severity="info" sx={{ mb: 1 }}>
                Provider model discovery is disabled. Add the model IDs you want to use.
                Adding an ID does not verify access. Saving and rotating credentials remain available.
              </Alert>
            )}
            {savedModelsFailed && (
              <Alert severity="warning" sx={{ mb: 1 }} action={
                <Button color="inherit" onClick={() => loadSavedModels(provider.name)}>
                  Retry saved models
                </Button>
              }>
                Saved models could not be loaded. Saving is blocked to avoid replacing
                existing routes with an incomplete list. Retry reads saved IDs only;
                it does not call the provider.
              </Alert>
            )}
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems="flex-start">
            <Autocomplete
              multiple
              freeSolo
              fullWidth
              autoSelect={PROVIDER_MODEL_DISCOVERY_ENABLED}
              inputValue={modelInput}
              onInputChange={(_, value) => setModelInput(value)}
              disableCloseOnSelect
              size="small"
              sx={{
                "&:hover .MuiAutocomplete-input, &.Mui-focused .MuiAutocomplete-input":
                  { minWidth: 30 },
              }}
              options={pickerOptions}
              value={models}
              onChange={(_, val) => {
                setModels(Array.from(new Set(normalizeModels(val))));
              }}
              renderOption={(props, option, { selected }) => (
                <li {...props} key={option}>
                  <Checkbox size="small" checked={selected} sx={{ mr: 1 }} />
                  <span>{option}</span>
                  {responsesOnly.has(option) && (
                    <EndpointTag label={RESPONSES_ONLY_TAG} />
                  )}
                  {decisionsOnly.has(option) && (
                    <EndpointTag label={DECISIONS_ONLY_TAG} />
                  )}
                </li>
              )}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => (
                  <Chip
                    variant="outlined"
                    label={
                      <>
                        <span>{option}</span>
                        {responsesOnly.has(option) && (
                          <EndpointTag label={RESPONSES_ONLY_TAG} />
                        )}
                        {decisionsOnly.has(option) && (
                          <EndpointTag label={DECISIONS_ONLY_TAG} />
                        )}
                      </>
                    }
                    size="small"
                    {...getTagProps({ index })}
                    key={option}
                  />
                ))
              }
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Model ID"
                  placeholder={
                    modelsLoading
                      ? (PROVIDER_MODEL_DISCOVERY_ENABLED ? "Fetching models..." : "Loading saved models...")
                      : modelOptions.length > 0
                        ? "Select models..."
                        : !PROVIDER_MODEL_DISCOVERY_ENABLED || isAwsAuth || isVertexAuth || isSystemOne
                          ? "Type or paste comma-separated model IDs"
                          : "Enter API key to load models, or type manually"
                  }
                  onPaste={(e) => {
                    const pasted = e.clipboardData.getData("text");
                    if (!/[,\n]/.test(pasted)) return;
                    e.preventDefault();
                    addManualModels(pasted);
                  }}
                />
              )}
            />
            {!PROVIDER_MODEL_DISCOVERY_ENABLED && (
              <Button
                variant="outlined"
                sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
                disabled={!modelInput.trim()}
                onClick={() => addManualModels(modelInput)}
              >
                Add model
              </Button>
            )}
            </Stack>
            {!PROVIDER_MODEL_DISCOVERY_ENABLED && (
              <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>
                {preset.modelIdHelp || "Use exact model IDs from your provider or endpoint documentation."}
                {" Paste multiple IDs separated by commas or new lines. "}
                {preset.docsUrl && (
                  <Link href={preset.docsUrl} target="_blank" rel="noopener noreferrer" underline="hover">
                    Model ID documentation
                  </Link>
                )}
              </Typography>
            )}
            {omittedModelCount > 0 && (
              <Alert severity="warning" sx={{ mt: 1 }}>
                {omittedModelCount} model IDs were omitted for safety.
              </Alert>
            )}
            {savedWriteOmittedCount > 0 && (
              <Alert severity="warning" sx={{ mt: 1 }}>
                Saved with {savedWriteOmittedCount} model IDs omitted for
                safety. Review the model list before saving again.
              </Alert>
            )}
            {showSavedBaseUrlNote && (
              <Alert severity="info" sx={{ mt: 1 }}>
                {SAVED_BASE_URL_MODEL_NOTE}
              </Alert>
            )}
            {fetchError && (
              <Alert
                severity="warning"
                variant="outlined"
                sx={{ mt: 1, py: 0 }}
              >
                {storedListFailed
                  ? `${asSentence(fetchError)} Save keeps this provider's current models.`
                  : fetchError}
              </Alert>
            )}
            {!isAwsAuth && !isVertexAuth && (PROVIDER_MODEL_DISCOVERY_ENABLED || isSystemOne) && (
              // Where the options come from — and, when any are tagged, which
              // endpoint they are served on. AWS and Vertex are left out:
              // neither is listed live.
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ mt: 0.5, display: "block" }}
              >
                {isSystemOne ? SYSTEM_ONE_MODELS_HELP : MODEL_LIST_SOURCE_HELP}
                {responsesOnly.size > 0 ? ` ${RESPONSES_ONLY_HELP}` : ""}
                {pickerOptions.some((m) => decisionsOnly.has(m))
                  ? ` ${DECISIONS_ONLY_HELP}`
                  : ""}
              </Typography>
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
                  ? (PROVIDER_MODEL_DISCOVERY_ENABLED
                    ? " — type a model ID and press Enter to add one manually."
                    : " — enter a model ID, then click Add model or press Enter.")
                  : "."}
              </Typography>
            )}
          </Box>

          <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1.5, p: 2 }}>
            <Button
              id={`${advancedId}-toggle`}
              aria-expanded={advancedExpanded}
              aria-controls={advancedId}
              aria-describedby={`${advancedId}-summary`}
              onClick={() => setAdvancedExpanded((expanded) => !expanded)}
              endIcon={<Box component="span" aria-hidden="true">{advancedExpanded ? "−" : "+"}</Box>}
              sx={{ px: 0, justifyContent: "space-between", width: "100%", textAlign: "left" }}
            >
              Advanced connection settings
            </Button>
            <Typography
              id={`${advancedId}-summary`}
              variant="body2"
              color="text.secondary"
              sx={{ overflowWrap: "anywhere" }}
            >
              {connectionSummary}
            </Typography>
            {/* Keep controlled fields mounted: disclosure changes presentation,
                never drafts, validation, discovery or the save payload. */}
            <Box id={advancedId} role="region" aria-labelledby={`${advancedId}-toggle`} hidden={!advancedExpanded}>
              <Stack spacing={2} sx={{ pt: 2 }}>
                {optionalBaseUrl && baseUrlField}
                {(() => {
                  const currentPreset = PROVIDER_PRESETS[name];
                  const visibleFormats = currentPreset?.supportedFormats?.length
                    ? API_FORMATS.filter((f) => currentPreset.supportedFormats.includes(f))
                    : API_FORMATS;
                  const isFiltered = visibleFormats.length < API_FORMATS.length;
                  const isSingleOption = visibleFormats.length === 1;
                  return (
                    <TextField
                      label="API Format"
                      select
                      fullWidth
                      value={apiFormat}
                      onChange={(e) => handleApiFormatChange(e.target.value)}
                      disabled={isSingleOption}
                      helperText={isFiltered ? "Restricted to compatible formats for this provider" : undefined}
                    >
                      {visibleFormats.map((f) => (
                        <MenuItem key={f} value={f}>{apiFormatLabel(f)}</MenuItem>
                      ))}
                    </TextField>
                  );
                })()}
                {apiFormat === "openai" && (
                  <TextField
                    label="API Path Prefix"
                    fullWidth
                    value={apiPathPrefix}
                    onChange={(e) => setApiPathPrefix(e.target.value)}
                    helperText="Leave blank when the provider endpoint is not versioned, such as Perplexity Sonar."
                  />
                )}
                <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                  <TextField
                    label="Timeout"
                    inputRef={(input) => { advancedInputRefs.current.timeout = input; }}
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
              </Stack>
            </Box>
          </Box>

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
          color="primary"
          onClick={handleSave}
          disabled={
            !name.trim() ||
            missingConnectedConfig ||
            (isAwsAuth && !awsRegion) ||
            updateProvider.isPending ||
            modelsLoading ||
            savedModelsFailed ||
            typedKeyUnverified ||
            (models.length === 0 && !keepsHeldBackModels)
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
                : "Add provider"}
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
  existingNames: PropTypes.arrayOf(PropTypes.string),
  existingProviders: PropTypes.objectOf(PropTypes.object),
};

export default AddProviderDialog;

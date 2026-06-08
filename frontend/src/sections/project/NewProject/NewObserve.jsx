import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  LinearProgress,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router-dom";
import { Events, handleOnDocsClicked, trackEvent } from "src/utils/Mixpanel";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import InstructionTitle from "./InstructionTitle";
import InstructionCodeCopy from "./InstructionCodeCopy";
import ObserveInstruments from "./ObserveInstuments";
import {
  CustomTab,
  CustomTabs,
  TabWrapper,
} from "src/sections/develop/AddDatasetDrawer/AddDatasetStyle";
import { persistObserveSetupIntent } from "src/sections/projects/observeOnboardingRoute";
import {
  OBSERVE_SETUP_LANGUAGE_VALUES as LANGUAGE_VALUES,
  defaultObserveSampleRequestCode as defaultSampleRequestCode,
  getObserveFallbackInstrumentDefinitions,
  getObserveInstrumentInstallCommand,
  getObserveRuntimeKeySetupCode as runtimeKeySetupCode,
  getObserveTraceTroubleshooting as traceTroubleshootingForInstrument,
  mergeObserveInstrumentDefinition as mergeInstrumentDefinition,
  normalizeObserveInstrumentId as normalizeInstrumentId,
  normalizeObserveSetupLanguage as normalizeSetupLanguage,
  observeAvailableInstrumentLanguages as availableInstrumentLanguages,
  observeFirstInstrumentLanguage as firstInstrumentLanguage,
  observeInstrumentSortRank as instrumentSortRank,
  observeInstrumentSupportsLanguage as instrumentSupportsLanguage,
  observeLanguageDataKey as languageDataKey,
} from "src/sections/projects/observeSetupCatalog";
import {
  appendSetupQuickStartAttributionToHref,
  readPersistedSetupQuickStartAttribution,
} from "src/sections/auth/jwt/setup-org-quick-starts";

const CODE_SECTION_ALIASES = {
  installationGuide: "installation_guide",
  projectAddCode: "project_add_code",
};

const ONBOARDING_SOURCE_VALUES = new Set(["onboarding", "onboarding_email"]);

const hasQuickStartAttribution = (attribution = {}) =>
  Boolean(attribution.quickStartId || attribution.quick_start_id);

const onboardingSetupReturnHref = ({
  instrumentId,
  language,
  quickStartAttribution,
} = {}) => {
  const params = new URLSearchParams({
    setup: "true",
    source: "onboarding",
    credential_step: "done",
  });
  if (instrumentId) params.set("provider", instrumentId);
  if (instrumentId && LANGUAGE_VALUES.has(language)) {
    params.set("language", language);
  }
  const attribution = hasQuickStartAttribution(quickStartAttribution)
    ? quickStartAttribution
    : readPersistedSetupQuickStartAttribution();
  return appendSetupQuickStartAttributionToHref(
    `/dashboard/observe?${params.toString()}`,
    attribution,
  );
};

const apiKeysOnboardingHref = ({
  instrumentId,
  language,
  quickStartAttribution,
} = {}) => {
  const params = new URLSearchParams({
    source: "onboarding",
    target: "observe_first_trace",
    action: "create",
    key_name: "Observe first trace",
    return_to: onboardingSetupReturnHref({
      instrumentId,
      language,
      quickStartAttribution,
    }),
  });
  return `${paths.dashboard.settings.apiKeys}?${params.toString()}`;
};

const FIRST_TRACE_STEPS = [
  {
    id: "package",
    label: "Choose package",
    description: "Choose the package that makes the model call in your app.",
  },
  {
    id: "setup",
    label: "Create keys",
    description: "Create Future AGI keys and keep your provider key loaded.",
  },
  {
    id: "run",
    label: "Run one request",
    description: "Paste the package code and run one request from that SDK.",
  },
  {
    id: "review",
    label: "Review trace",
    description: "Inspect the trace details when Future AGI opens it.",
  },
  {
    id: "evaluator",
    label: "Create quality check",
    description: "Turn the reviewed trace into a repeatable check.",
  },
];

const VerificationAlert = ({ setupVerification }) => {
  if (!setupVerification) return null;

  return (
    <Alert
      data-testid="observe-setup-verification"
      severity={setupVerification.status === "ready" ? "success" : "info"}
      icon={
        setupVerification.status === "waiting" ? (
          <CircularProgress size={18} />
        ) : undefined
      }
      action={
        setupVerification.primaryAction ? (
          <Button
            color="inherit"
            size="small"
            onClick={setupVerification.primaryAction.onClick}
            disabled={setupVerification.primaryAction.disabled}
          >
            {setupVerification.primaryAction.label}
          </Button>
        ) : null
      }
      sx={{ alignItems: "center" }}
    >
      <Stack spacing={0.25}>
        <Typography variant="subtitle2">{setupVerification.title}</Typography>
        <Typography variant="body2">{setupVerification.description}</Typography>
      </Stack>
    </Alert>
  );
};

VerificationAlert.propTypes = {
  setupVerification: PropTypes.shape({
    description: PropTypes.string.isRequired,
    primaryAction: PropTypes.shape({
      disabled: PropTypes.bool,
      label: PropTypes.string.isRequired,
      onClick: PropTypes.func.isRequired,
    }),
    status: PropTypes.oneOf(["ready", "waiting"]).isRequired,
    title: PropTypes.string.isRequired,
  }),
};

const CurrentSetupTask = ({
  apiKeysHref,
  credentialsCopied,
  hasSelectedInstrument,
  selectedInstrumentLabel,
  selectedLanguageLabel,
  setupVerification,
}) => {
  if (!hasSelectedInstrument) {
    return (
      <Alert
        severity="info"
        icon={<Iconify icon="mdi:package-variant-closed" width={20} />}
        data-testid="observe-current-setup-task"
        sx={{ alignItems: "flex-start" }}
      >
        <Stack spacing={0.25}>
          <Typography variant="subtitle2">Choose your package</Typography>
          <Typography variant="body2">
            Select the SDK that sends the model request. The setup code changes
            to match that package before anything else is shown.
          </Typography>
        </Stack>
      </Alert>
    );
  }

  if (!credentialsCopied) {
    return (
      <Alert
        severity="info"
        icon={<Iconify icon="mdi:key-outline" width={20} />}
        action={
          <Button
            color="inherit"
            size="small"
            component={RouterLink}
            href={apiKeysHref}
          >
            Create key
          </Button>
        }
        data-testid="observe-current-setup-task"
        sx={{ alignItems: "center" }}
      >
        <Stack spacing={0.25}>
          <Typography variant="subtitle2">
            Next: create a Future AGI API key
          </Typography>
          <Typography variant="body2">
            Then return here and run the {selectedInstrumentLabel}{" "}
            {selectedLanguageLabel} example with your provider key loaded.
          </Typography>
        </Stack>
      </Alert>
    );
  }

  return (
    <Alert
      severity={setupVerification?.status === "ready" ? "success" : "info"}
      icon={
        setupVerification?.status === "ready" ? undefined : (
          <CircularProgress size={18} />
        )
      }
      action={
        setupVerification?.primaryAction ? (
          <Button
            color="inherit"
            size="small"
            onClick={setupVerification.primaryAction.onClick}
            disabled={setupVerification.primaryAction.disabled}
          >
            {setupVerification.primaryAction.label}
          </Button>
        ) : null
      }
      data-testid="observe-current-setup-task"
      sx={{ alignItems: "center" }}
    >
      <Stack spacing={0.25}>
        <Typography variant="subtitle2">
          {setupVerification?.status === "ready"
            ? "Trace detected"
            : `Waiting for ${selectedInstrumentLabel} ${selectedLanguageLabel} trace`}
        </Typography>
        <Typography variant="body2">
          {setupVerification?.status === "ready"
            ? "Open the trace review, then create the first quality check from it."
            : `Run one request with the ${selectedInstrumentLabel} code below. Keep this page open; Future AGI checks every few seconds, opens trace review when data arrives, then guides the first quality check.`}
        </Typography>
      </Stack>
    </Alert>
  );
};

CurrentSetupTask.propTypes = {
  apiKeysHref: PropTypes.string.isRequired,
  credentialsCopied: PropTypes.bool,
  hasSelectedInstrument: PropTypes.bool.isRequired,
  selectedInstrumentLabel: PropTypes.string.isRequired,
  selectedLanguageLabel: PropTypes.string.isRequired,
  setupVerification: VerificationAlert.propTypes.setupVerification,
};

const FirstTraceSetupGuide = ({
  credentialsCopied,
  completeSetupCode,
  getCodeBySection,
  instrumentCode,
  instrumentInstallCode,
  instrumentRuntimeKeyCode,
  instrumentSampleRequestCode,
  instrumentOptions,
  apiKeysHref,
  languageTab,
  onLanguageChange,
  onInstrumentChange,
  requestedInstrumentMissing,
  selectedInstrument,
  selectedInstrumentLanguage,
  setupVerification,
  tabOptions,
  tabWrapperStyles,
  theme,
}) => {
  const hasSelectedInstrument = Boolean(selectedInstrument);
  const statusLabel =
    setupVerification?.status === "ready"
      ? "Trace detected"
      : "Checking for trace";
  const selectedInstrumentLabel = selectedInstrument?.name || "your package";
  const selectedLanguageLabel =
    selectedInstrumentLanguage === "typescript" ? "TypeScript" : "Python";
  const traceTroubleshooting = traceTroubleshootingForInstrument(
    selectedInstrument?.id,
  );
  const projectKeysCode = getCodeBySection("keys");
  const projectRegistrationCode = getCodeBySection("projectAddCode");

  return (
    <Box
      data-testid="observe-first-trace-guide"
      sx={{
        border: "1px solid",
        borderColor: "primary.main",
        borderRadius: 1,
        bgcolor: "action.hover",
        p: 2,
      }}
    >
      <Stack spacing={2}>
        <Stack
          direction={{ xs: "column", md: "row" }}
          spacing={1}
          justifyContent="space-between"
          alignItems={{ xs: "flex-start", md: "center" }}
        >
          <Stack spacing={0.5}>
            <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
              <Chip size="small" color="primary" label="Setup guide" />
              <Chip size="small" variant="outlined" label={statusLabel} />
            </Stack>
            <Typography variant="h6">
              {hasSelectedInstrument
                ? `Connect ${selectedInstrumentLabel}, then send one trace`
                : "Choose package, then send one trace"}
            </Typography>
            <Typography variant="body2" color="text.secondary" maxWidth={720}>
              {hasSelectedInstrument
                ? `These snippets match ${selectedInstrumentLabel} in ${selectedLanguageLabel}. Run one request and keep this page open; we move you to trace review when it arrives, then guide you to create the first quality check.`
                : "Pick the SDK package that makes the model call. We will then show the matching install command, setup code, request example, and trace checks."}
            </Typography>
          </Stack>

          {hasSelectedInstrument ? (
            <TabWrapper sx={tabWrapperStyles}>
              <CustomTabs
                textColor="primary"
                value={languageTab}
                onChange={(e, value) => onLanguageChange(value)}
                TabIndicatorProps={{
                  style: {
                    backgroundColor: theme.palette.primary.main,
                    opacity: 0.08,
                    height: "100%",
                    borderRadius: "8px",
                  },
                }}
              >
                {tabOptions.map((tab) => (
                  <CustomTab
                    key={`first-trace-${tab.value}`}
                    label={tab.label}
                    value={tab.value}
                    disabled={tab.disabled}
                  />
                ))}
              </CustomTabs>
            </TabWrapper>
          ) : null}
        </Stack>

        {hasSelectedInstrument ? (
          <Alert
            severity="info"
            icon={<Iconify icon="mdi:code-tags" width={20} />}
            data-testid="observe-package-specific-code-alert"
            sx={{ alignItems: "flex-start" }}
          >
            <Stack spacing={0.25}>
              <Typography variant="subtitle2">
                {selectedInstrumentLabel} {selectedLanguageLabel} code selected
              </Typography>
              <Typography variant="body2">
                The install, package setup, request, and trace checks below are
                for {selectedInstrumentLabel}. Switch the package if your model
                call uses another SDK.
              </Typography>
            </Stack>
          </Alert>
        ) : null}

        <CurrentSetupTask
          apiKeysHref={apiKeysHref}
          credentialsCopied={credentialsCopied}
          hasSelectedInstrument={hasSelectedInstrument}
          selectedInstrumentLabel={selectedInstrumentLabel}
          selectedLanguageLabel={selectedLanguageLabel}
          setupVerification={setupVerification}
        />

        {instrumentOptions.length ? (
          <Stack spacing={1}>
            <Stack spacing={0.25}>
              <Typography variant="subtitle2">
                Which package does your app use?
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Pick the library that creates the model calls. The code below
                changes to that package.
              </Typography>
            </Stack>
            <Stack
              direction="row"
              spacing={1}
              flexWrap="wrap"
              useFlexGap
              data-testid="observe-instrument-picker"
            >
              {instrumentOptions.map((instrument) => {
                const isSelected = instrument.id === selectedInstrument?.id;
                return (
                  <Button
                    key={instrument.id}
                    size="small"
                    variant={isSelected ? "contained" : "outlined"}
                    color={isSelected ? "primary" : "inherit"}
                    onClick={() => onInstrumentChange(instrument.id)}
                    data-testid={`observe-instrument-option-${instrument.id}`}
                    startIcon={
                      instrument.logo ? (
                        <Box
                          component="img"
                          src={instrument.logo}
                          alt=""
                          sx={{ width: 18, height: 18, objectFit: "contain" }}
                        />
                      ) : null
                    }
                    sx={{ textTransform: "none" }}
                  >
                    {instrument.name}
                  </Button>
                );
              })}
            </Stack>
          </Stack>
        ) : null}

        {requestedInstrumentMissing ? (
          <Alert severity="warning" sx={{ alignItems: "center" }}>
            <Typography variant="body2">
              The requested package is not available in this setup response.
              Showing the default package setup instead.
            </Typography>
          </Alert>
        ) : null}

        {!hasSelectedInstrument ? (
          <Alert
            severity="info"
            icon={<Iconify icon="mdi:package-variant-closed" width={20} />}
            data-testid="observe-package-required"
            sx={{ alignItems: "flex-start" }}
          >
            <Stack spacing={0.25}>
              <Typography variant="subtitle2">
                Choose the package your app uses
              </Typography>
              <Typography variant="body2">
                We will only show setup code after a package is selected, so the
                snippet matches the SDK that sends your model request.
              </Typography>
            </Stack>
          </Alert>
        ) : null}

        {!hasSelectedInstrument ? null : (
          <>
            <Box
              data-testid="observe-first-trace-steps"
              sx={{
                display: "grid",
                gridTemplateColumns: {
                  xs: "1fr",
                  md: "repeat(2, minmax(0, 1fr))",
                  xl: "repeat(5, minmax(0, 1fr))",
                },
                gap: 1,
              }}
            >
              {FIRST_TRACE_STEPS.map((step, index) => (
                <Box
                  key={step.id}
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    bgcolor: "background.paper",
                    p: 1.25,
                    minHeight: 96,
                  }}
                >
                  <Stack spacing={0.75}>
                    <Stack direction="row" spacing={0.75} alignItems="center">
                      <Chip size="small" label={index + 1} />
                      <Typography variant="subtitle2">{step.label}</Typography>
                    </Stack>
                    <Typography variant="body2" color="text.secondary">
                      {step.description}
                    </Typography>
                  </Stack>
                </Box>
              ))}
            </Box>

            <Stack spacing={1} sx={{ minWidth: 0 }}>
              <Typography variant="subtitle2">
                Copy complete {selectedInstrumentLabel} {selectedLanguageLabel}{" "}
                example
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Copy these blocks in order. Load the keys in the process that
                runs your app, then run the package request so Future AGI can
                detect the first trace.
              </Typography>
              <InstructionCodeCopy
                ariaLabel="Copy complete package setup"
                text={completeSetupCode}
                language={selectedInstrumentLanguage}
              />
            </Stack>

            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: { xs: "1fr", lg: "repeat(2, 1fr)" },
                gap: 1.5,
                minWidth: 0,
              }}
            >
              <Stack spacing={1} sx={{ minWidth: 0 }}>
                <Typography variant="subtitle2">
                  1. Install {selectedInstrumentLabel}
                </Typography>
                <InstructionCodeCopy
                  ariaLabel="Copy install command"
                  text={instrumentInstallCode}
                  language={selectedInstrumentLanguage}
                />
              </Stack>
              <Stack spacing={1} sx={{ minWidth: 0 }}>
                <Stack
                  direction={{ xs: "column", sm: "row" }}
                  spacing={1}
                  alignItems={{ xs: "flex-start", sm: "center" }}
                  justifyContent="space-between"
                >
                  <Typography variant="subtitle2">
                    2. Load Future AGI and provider keys
                  </Typography>
                  <Button
                    size="small"
                    variant="outlined"
                    component={RouterLink}
                    href={apiKeysHref}
                    startIcon={<Iconify icon="mdi:key-outline" width={16} />}
                    sx={{ alignSelf: { xs: "stretch", sm: "flex-start" } }}
                  >
                    {credentialsCopied
                      ? "Create another key"
                      : "Create API key"}
                  </Button>
                </Stack>
                {credentialsCopied ? (
                  <Alert severity="success" icon={false} sx={{ py: 0.5 }}>
                    <Typography variant="caption">
                      Credentials copied. Paste both values into the snippet,
                      then run one request.
                    </Typography>
                  </Alert>
                ) : (
                  <Typography variant="caption" color="text.secondary">
                    Create a Future AGI API key and secret key before running
                    the snippet.
                  </Typography>
                )}
                {projectKeysCode ? (
                  <InstructionCodeCopy
                    ariaLabel="Copy project keys"
                    text={projectKeysCode}
                    language={languageTab}
                  />
                ) : null}
                {instrumentRuntimeKeyCode ? (
                  <>
                    <Typography variant="caption" color="text.secondary">
                      Also load the {selectedInstrumentLabel} runtime key in the
                      same shell or process that runs the request.
                    </Typography>
                    <InstructionCodeCopy
                      ariaLabel={`Copy ${selectedInstrumentLabel} runtime keys`}
                      text={instrumentRuntimeKeyCode}
                      language="bash"
                    />
                  </>
                ) : null}
                {projectRegistrationCode ? (
                  <InstructionCodeCopy
                    ariaLabel="Copy project registration"
                    text={projectRegistrationCode}
                    language={languageTab}
                  />
                ) : null}
              </Stack>
              <Stack spacing={1} sx={{ minWidth: 0 }}>
                <Typography variant="subtitle2">
                  3. Connect {selectedInstrumentLabel}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Paste this before importing or creating the client, then run
                  one request in your app.
                </Typography>
                <InstructionCodeCopy
                  ariaLabel="Copy package setup code"
                  text={instrumentCode}
                  language={selectedInstrumentLanguage}
                />
              </Stack>
              <Stack spacing={1} sx={{ minWidth: 0 }}>
                <Typography variant="subtitle2">
                  4. Run one {selectedInstrumentLabel} request
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Use this ready-to-run request if you do not have a local
                  request ready. Keep this page open after it runs; Future AGI
                  waits for the trace, opens review when it arrives, then points
                  you to the first quality check.
                </Typography>
                <InstructionCodeCopy
                  ariaLabel="Copy package request"
                  text={instrumentSampleRequestCode}
                  language={selectedInstrumentLanguage}
                />
              </Stack>
            </Box>

            <VerificationAlert setupVerification={setupVerification} />

            <Alert
              severity="info"
              icon={<Iconify icon="mdi:help-circle-outline" width={20} />}
              data-testid="observe-trace-troubleshooting"
              sx={{ alignItems: "flex-start" }}
            >
              <Stack spacing={0.75}>
                <Typography variant="subtitle2">
                  {traceTroubleshooting.title}
                </Typography>
                <Stack spacing={0.5}>
                  {traceTroubleshooting.checks.map((check) => (
                    <Typography key={check} variant="body2">
                      {check}
                    </Typography>
                  ))}
                </Stack>
              </Stack>
            </Alert>
          </>
        )}
      </Stack>
    </Box>
  );
};

FirstTraceSetupGuide.propTypes = {
  completeSetupCode: PropTypes.string.isRequired,
  credentialsCopied: PropTypes.bool,
  getCodeBySection: PropTypes.func.isRequired,
  instrumentCode: PropTypes.string.isRequired,
  instrumentInstallCode: PropTypes.string.isRequired,
  instrumentRuntimeKeyCode: PropTypes.string,
  instrumentSampleRequestCode: PropTypes.string.isRequired,
  instrumentOptions: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      logo: PropTypes.string,
      name: PropTypes.string.isRequired,
    }),
  ).isRequired,
  apiKeysHref: PropTypes.string.isRequired,
  languageTab: PropTypes.string.isRequired,
  onLanguageChange: PropTypes.func.isRequired,
  onInstrumentChange: PropTypes.func.isRequired,
  requestedInstrumentMissing: PropTypes.bool,
  selectedInstrument: PropTypes.object,
  selectedInstrumentLanguage: PropTypes.string.isRequired,
  setupVerification: VerificationAlert.propTypes.setupVerification,
  tabOptions: PropTypes.arrayOf(
    PropTypes.shape({
      disabled: PropTypes.bool,
      label: PropTypes.string.isRequired,
      value: PropTypes.string.isRequired,
    }),
  ).isRequired,
  tabWrapperStyles: PropTypes.object.isRequired,
  theme: PropTypes.object.isRequired,
};

const NewObserve = ({ setupVerification, showFirstTraceGuide = false }) => {
  const theme = useTheme();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedLanguage = normalizeSetupLanguage(
    searchParams.get("language") || searchParams.get("lang"),
  );
  const initialLanguage = requestedLanguage || "python";
  const [languageTab, setLanguageTab] = useState(initialLanguage);
  const credentialsCopied =
    searchParams.get("credential_step") === "done" &&
    ONBOARDING_SOURCE_VALUES.has(searchParams.get("source"));
  const requestedInstrumentId = normalizeInstrumentId(
    searchParams.get("instrument") ||
      searchParams.get("package") ||
      searchParams.get("provider"),
  );
  const quickStartAttribution = useMemo(
    () => ({
      quickStartGoal: searchParams.get("quick_start_goal"),
      quickStartId: searchParams.get("quick_start_id"),
      quickStartPrimaryPath: searchParams.get("quick_start_primary_path"),
    }),
    [searchParams],
  );
  const [selectedInstrumentId, setSelectedInstrumentId] = useState(
    requestedInstrumentId || null,
  );
  const requestedInstrumentAppliedRef = useRef(null);
  const {
    data: keysData,
    isSuccess,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["keys"],
    queryFn: () =>
      axios.get(endpoints.project.getCodeBlockTracer, {
        params: {
          project_type: "observe",
        },
      }),
    select: (d) => d.data?.result,
  });

  const tabOptions = [
    { label: "Python", value: "python", disabled: false },
    { label: "TypeScript", value: "typescript", disabled: false },
  ];

  const tabWrapperStyles = useMemo(
    () => ({
      marginBottom: 0,
      alignSelf: "flex-start",
    }),
    [],
  );

  const cleanCode = (code) => {
    if (typeof code !== "string") return "";
    const normalized = code.replace(/^\n+/, "").replace(/\n+$/, "");
    return normalized.trim() ? normalized : "";
  };

  // Helper functions to get the correct code based on active tabs
  const getCodeBySection = (section) => {
    const languageKey = languageTab === "python" ? "Python" : "TypeScript";
    const sectionData =
      keysData?.[section] || keysData?.[CODE_SECTION_ALIASES[section]];
    return cleanCode(sectionData?.[languageKey]);
  };

  const instrumentOptions = useMemo(() => {
    const fallbackInstrumentDefinitions =
      getObserveFallbackInstrumentDefinitions();
    const mergedInstruments = new Map(
      Object.keys(fallbackInstrumentDefinitions).map((id) => [
        id,
        mergeInstrumentDefinition(id),
      ]),
    );
    Object.entries(keysData?.instruments || {}).forEach(([id, instrument]) => {
      const normalizedId = normalizeInstrumentId(id);
      if (!normalizedId || !instrument || typeof instrument !== "object") {
        return;
      }
      mergedInstruments.set(
        normalizedId,
        mergeInstrumentDefinition(normalizedId, instrument),
      );
    });

    return Array.from(mergedInstruments.values())
      .filter((instrument) => availableInstrumentLanguages(instrument).length)
      .sort((left, right) => {
        const rankDiff =
          instrumentSortRank(left.id) - instrumentSortRank(right.id);
        if (rankDiff !== 0) return rankDiff;
        return String(left.name).localeCompare(String(right.name));
      });
  }, [keysData?.instruments]);
  const requestedInstrumentMissing = Boolean(
    requestedInstrumentId &&
      instrumentOptions.length &&
      !instrumentOptions.some(
        (instrument) => instrument.id === requestedInstrumentId,
      ),
  );
  const selectedInstrument = useMemo(() => {
    if (!instrumentOptions.length) return null;
    if (showFirstTraceGuide && !selectedInstrumentId) return null;
    return (
      instrumentOptions.find(
        (instrument) => instrument.id === selectedInstrumentId,
      ) || instrumentOptions[0]
    );
  }, [instrumentOptions, selectedInstrumentId, showFirstTraceGuide]);
  const selectedInstrumentLanguage = selectedInstrument
    ? instrumentSupportsLanguage(selectedInstrument, languageTab)
      ? languageTab
      : firstInstrumentLanguage(selectedInstrument)
    : languageTab;
  const selectedInstrumentLanguageKey = languageDataKey(
    selectedInstrumentLanguage,
  );
  const selectedInstrumentCode =
    selectedInstrument?.[selectedInstrumentLanguageKey]?.code;
  const instrumentCode = cleanCode(
    selectedInstrumentCode ||
      `Package setup code is not available for ${selectedInstrument?.name || "this package"} in ${
        selectedInstrumentLanguage === "typescript" ? "TypeScript" : "Python"
      }. Choose another package or language.`,
  );
  const instrumentInstallCode =
    getObserveInstrumentInstallCommand({
      instrumentId: selectedInstrument?.id,
      language: selectedInstrumentLanguage,
    }) || getCodeBySection("installationGuide");
  const instrumentRuntimeKeyCode = runtimeKeySetupCode(
    selectedInstrument?.id,
    selectedInstrumentLanguage,
  );
  const instrumentSampleRequestCode = cleanCode(
    selectedInstrument?.[selectedInstrumentLanguageKey]?.sample_request_code ||
      defaultSampleRequestCode({
        instrumentName: selectedInstrument?.name,
        language: selectedInstrumentLanguage,
      }),
  );
  const completeSetupCode = cleanCode(
    [
      getCodeBySection("keys"),
      runtimeKeySetupCode(selectedInstrument?.id, selectedInstrumentLanguage),
      getCodeBySection("projectAddCode"),
      instrumentCode,
      instrumentSampleRequestCode,
    ]
      .filter(Boolean)
      .join("\n\n"),
  );
  const apiKeysHref = apiKeysOnboardingHref({
    instrumentId: selectedInstrument?.id,
    language: selectedInstrumentLanguage,
    quickStartAttribution,
  });
  const firstTraceTabOptions = tabOptions.map((tab) => ({
    ...tab,
    disabled: selectedInstrument
      ? !instrumentSupportsLanguage(selectedInstrument, tab.value)
      : tab.disabled,
  }));

  const syncSetupIntentToUrl = useCallback(
    ({ instrumentId, language } = {}) => {
      if (!showFirstTraceGuide || !instrumentId) return;

      const nextParams = new URLSearchParams(searchParams);
      const isSetupRoute =
        nextParams.get("setup") === "true" ||
        ONBOARDING_SOURCE_VALUES.has(nextParams.get("source")) ||
        Boolean(nextParams.get("journey_step"));
      if (!isSetupRoute) return;

      nextParams.set("provider", instrumentId);
      if (LANGUAGE_VALUES.has(language)) {
        nextParams.set("language", language);
      }

      if (nextParams.toString() !== searchParams.toString()) {
        setSearchParams(nextParams, { replace: true });
      }
    },
    [searchParams, setSearchParams, showFirstTraceGuide],
  );

  useEffect(() => {
    if (!instrumentOptions.length) return;
    const requestedOption = instrumentOptions.find(
      (instrument) => instrument.id === requestedInstrumentId,
    );
    const currentOption = instrumentOptions.find(
      (instrument) => instrument.id === selectedInstrumentId,
    );
    if (
      requestedOption &&
      requestedInstrumentAppliedRef.current !== requestedInstrumentId
    ) {
      requestedInstrumentAppliedRef.current = requestedInstrumentId;
      setSelectedInstrumentId(requestedOption.id);
      return;
    }
    if (!currentOption && (!showFirstTraceGuide || selectedInstrumentId)) {
      setSelectedInstrumentId(instrumentOptions[0].id);
    }
  }, [
    instrumentOptions,
    requestedInstrumentId,
    selectedInstrumentId,
    showFirstTraceGuide,
  ]);

  useEffect(() => {
    if (!selectedInstrument) return;
    if (instrumentSupportsLanguage(selectedInstrument, languageTab)) return;
    setLanguageTab(firstInstrumentLanguage(selectedInstrument));
  }, [languageTab, selectedInstrument]);

  useEffect(() => {
    if (!selectedInstrument) return;
    syncSetupIntentToUrl({
      instrumentId: selectedInstrument.id,
      language: selectedInstrumentLanguage,
    });
  }, [selectedInstrument, selectedInstrumentLanguage, syncSetupIntentToUrl]);

  useEffect(() => {
    if (!showFirstTraceGuide || !selectedInstrument) return;
    persistObserveSetupIntent({
      setupLanguage: selectedInstrumentLanguage,
      setupProvider: selectedInstrument.id,
    });
  }, [selectedInstrument, selectedInstrumentLanguage, showFirstTraceGuide]);

  const handleInstrumentChange = (instrumentId) => {
    const nextInstrument = instrumentOptions.find(
      (instrument) => instrument.id === instrumentId,
    );
    const nextLanguage =
      nextInstrument && !instrumentSupportsLanguage(nextInstrument, languageTab)
        ? firstInstrumentLanguage(nextInstrument)
        : languageTab;
    setSelectedInstrumentId(instrumentId);
    if (nextLanguage !== languageTab) {
      setLanguageTab(nextLanguage);
    }
    syncSetupIntentToUrl({ instrumentId, language: nextLanguage });
  };

  const handleLanguageChange = (nextLanguage) => {
    setLanguageTab(nextLanguage);
    syncSetupIntentToUrl({
      instrumentId: selectedInstrument?.id || selectedInstrumentId,
      language: nextLanguage,
    });
  };

  return (
    <Box
      sx={{
        width: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 4, // 32px spacing between major sections
      }}
    >
      {setupVerification &&
      (!showFirstTraceGuide || !isSuccess || !keysData) ? (
        <VerificationAlert setupVerification={setupVerification} />
      ) : null}

      {!isSuccess || !keysData ? <LinearProgress /> : null}

      {isSuccess && keysData ? (
        <>
          {showFirstTraceGuide ? (
            <FirstTraceSetupGuide
              completeSetupCode={completeSetupCode}
              credentialsCopied={credentialsCopied}
              getCodeBySection={getCodeBySection}
              instrumentCode={instrumentCode}
              instrumentInstallCode={instrumentInstallCode}
              instrumentRuntimeKeyCode={instrumentRuntimeKeyCode}
              instrumentSampleRequestCode={instrumentSampleRequestCode}
              instrumentOptions={instrumentOptions}
              apiKeysHref={apiKeysHref}
              languageTab={languageTab}
              onLanguageChange={handleLanguageChange}
              onInstrumentChange={handleInstrumentChange}
              requestedInstrumentMissing={requestedInstrumentMissing}
              selectedInstrument={selectedInstrument}
              selectedInstrumentLanguage={selectedInstrumentLanguage}
              setupVerification={setupVerification}
              tabOptions={firstTraceTabOptions}
              tabWrapperStyles={tabWrapperStyles}
              theme={theme}
            />
          ) : null}

          {!showFirstTraceGuide ? (
            <>
              {/* Installation & Keys Section */}
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
                <InstructionTitle
                  title="Install Dependencies"
                  description="For more instructions, checkout our "
                  url="https://docs.futureagi.com/docs/observe"
                  urltext="Docs"
                  onUrlClick={() => handleOnDocsClicked("observe_page")}
                />

                <InstructionTitle description="Configure your application to send traces to Future AGI" />

                {/* Tab selector for installation */}
                <TabWrapper sx={tabWrapperStyles}>
                  <CustomTabs
                    textColor="primary"
                    value={languageTab}
                    onChange={(e, value) => setLanguageTab(value)}
                    TabIndicatorProps={{
                      style: {
                        backgroundColor: theme.palette.primary.main,
                        opacity: 0.08,
                        height: "100%",
                        borderRadius: "8px",
                      },
                    }}
                  >
                    {tabOptions.map((tab) => (
                      <CustomTab
                        key={`config-${tab.value}`}
                        label={tab.label}
                        value={tab.value}
                        disabled={tab.disabled}
                      />
                    ))}
                  </CustomTabs>
                </TabWrapper>

                <InstructionCodeCopy
                  ariaLabel="Copy install command"
                  text={getCodeBySection("installationGuide")}
                  language={languageTab}
                  // onCopy={() => trackEvent(Events.installDependenciesCopied)}
                />

                {/* API Keys */}
                <Box sx={{ mt: 1.5 }}>
                  <InstructionTitle
                    title="Load API keys"
                    description="load your API keys"
                  />
                </Box>

                <InstructionCodeCopy
                  ariaLabel="Copy API keys"
                  text={getCodeBySection("keys")}
                  language={languageTab}
                  onCopy={() => trackEvent(Events.apikeys)}
                />
              </Box>

              {/* Project registration section with its own tab control */}
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
                <InstructionTitle
                  title="Register project"
                  description="Register your application before the package call so traces are sent to this project."
                />

                {/* Separate tab selector for project registration */}
                <TabWrapper sx={tabWrapperStyles}>
                  <CustomTabs
                    textColor="primary"
                    value={languageTab}
                    onChange={(e, value) => setLanguageTab(value)}
                    TabIndicatorProps={{
                      style: {
                        backgroundColor: theme.palette.primary.main,
                        opacity: 0.08,
                        height: "100%",
                        borderRadius: "8px",
                      },
                    }}
                  >
                    {tabOptions.map((tab) => (
                      <CustomTab
                        key={`telemetry-${tab.value}`}
                        label={tab.label}
                        value={tab.value}
                        disabled={tab.disabled}
                      />
                    ))}
                  </CustomTabs>
                </TabWrapper>

                <InstructionCodeCopy
                  ariaLabel="Copy project registration"
                  text={getCodeBySection("projectAddCode")}
                  language={languageTab}
                  // onCopy={() => trackEvent(Events.setupTelemetryCopied)}
                />
              </Box>

              {/* Package setup section */}
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
                <InstructionTitle
                  title="Connect package calls"
                  description="Connect the package that makes AI calls so Future AGI can trace the request."
                />

                <ObserveInstruments
                  data={keysData?.instruments}
                  isLoading={isLoading}
                  isSuccess={isSuccess}
                  error={error}
                  languageTab={languageTab}
                  onLanguageChange={setLanguageTab}
                />
              </Box>
            </>
          ) : null}
        </>
      ) : null}
    </Box>
  );
};

NewObserve.propTypes = {
  showFirstTraceGuide: PropTypes.bool,
  setupVerification: PropTypes.shape({
    description: PropTypes.string.isRequired,
    primaryAction: PropTypes.shape({
      disabled: PropTypes.bool,
      label: PropTypes.string.isRequired,
      onClick: PropTypes.func.isRequired,
    }),
    status: PropTypes.oneOf(["ready", "waiting"]).isRequired,
    title: PropTypes.string.isRequired,
  }),
};

export default NewObserve;

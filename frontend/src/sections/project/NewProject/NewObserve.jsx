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

const CODE_SECTION_ALIASES = {
  installationGuide: "installation_guide",
  projectAddCode: "project_add_code",
};

const ONBOARDING_SOURCE_VALUES = new Set(["onboarding", "onboarding_email"]);
const LANGUAGE_VALUES = new Set(["python", "typescript"]);

const normalizeSetupValue = (value) =>
  typeof value === "string"
    ? value.trim().toLowerCase().replaceAll("-", "_")
    : "";

const normalizeSetupLanguage = (value) => {
  const normalizedValue = normalizeSetupValue(value);
  return LANGUAGE_VALUES.has(normalizedValue) ? normalizedValue : "";
};

const normalizeInstrumentId = (value) => {
  const normalizedValue = normalizeSetupValue(value);
  if (normalizedValue === "llama_index") return "llamaindex";
  if (normalizedValue === "openaiagents") return "openai_agents";
  return normalizedValue;
};

const onboardingSetupReturnHref = ({ instrumentId, language } = {}) => {
  const params = new URLSearchParams({
    setup: "true",
    source: "onboarding",
    credential_step: "done",
  });
  if (instrumentId) params.set("provider", instrumentId);
  if (instrumentId && LANGUAGE_VALUES.has(language)) {
    params.set("language", language);
  }
  return `/dashboard/observe?${params.toString()}`;
};

const apiKeysOnboardingHref = ({ instrumentId, language } = {}) => {
  const params = new URLSearchParams({
    source: "onboarding",
    target: "observe_first_trace",
    action: "create",
    key_name: "Observe first trace",
    return_to: onboardingSetupReturnHref({ instrumentId, language }),
  });
  return `${paths.dashboard.settings.apiKeys}?${params.toString()}`;
};

const FIRST_TRACE_STEPS = [
  {
    id: "package",
    label: "Pick package",
    description: "Choose the library that creates the model call.",
  },
  {
    id: "setup",
    label: "Paste setup",
    description:
      "Install tracing, load Future AGI keys, and register the project.",
  },
  {
    id: "run",
    label: "Run package request",
    description:
      "Use your app request or the package-specific smoke test below.",
  },
  {
    id: "review",
    label: "Review and add eval",
    description: "We open the trace, then guide you to create an evaluator.",
  },
];

const SETUP_INSTRUMENT_PRIORITY = [
  "openai",
  "anthropic",
  "langchain",
  "openai_agents",
  "llamaindex",
  "bedrock",
  "mcp",
];

const INSTRUMENT_INSTALL_COMMANDS = {
  python: {
    anthropic: "pip install traceAI-anthropic anthropic",
    bedrock: "pip install traceAI-bedrock",
    langchain: "pip install traceAI-langchain",
    mcp: "pip install traceAI-mcp",
    openai: "pip install traceAI-openai openai",
    openai_agents: "pip install traceAI-openai-agents",
  },
  typescript: {
    anthropic:
      "npm install @traceai/fi-core @traceai/anthropic @opentelemetry/instrumentation @anthropic-ai/sdk",
    langchain:
      "npm install @traceai/fi-core @traceai/langchain @opentelemetry/instrumentation",
    mcp: "npm install @traceai/fi-core @traceai/mcp @opentelemetry/instrumentation",
    openai:
      "npm install @traceai/fi-core @traceai/openai @opentelemetry/instrumentation openai",
    openai_agents:
      "npm install @traceai/fi-core @traceai/openai-agents @opentelemetry/instrumentation",
  },
};

const defaultSampleRequestCode = ({ instrumentName, language }) => {
  const packageName = instrumentName || "your package";
  if (language === "typescript") {
    return `// Run one request in the code path that uses ${packageName}.
// Keep Future AGI open; the trace appears after the request completes.
await runYourExisting${packageName.replace(/[^A-Za-z0-9]/g, "") || "AI"}Request();`;
  }
  return `# Run one request in the code path that uses ${packageName}.
# Keep Future AGI open; the trace appears after the request completes.
run_your_existing_${
    packageName
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "") || "ai"
  }_request()`;
};

const languageDataKey = (language) =>
  language === "typescript" ? "TypeScript" : "Python";

const instrumentSupportsLanguage = (instrument, language) =>
  Boolean(instrument?.[languageDataKey(language)]?.code);

const availableInstrumentLanguages = (instrument) =>
  ["python", "typescript"].filter((language) =>
    instrumentSupportsLanguage(instrument, language),
  );

const firstInstrumentLanguage = (instrument) =>
  availableInstrumentLanguages(instrument)[0] || "python";

const instrumentSortRank = (id) => {
  const index = SETUP_INSTRUMENT_PRIORITY.indexOf(id);
  return index === -1 ? SETUP_INSTRUMENT_PRIORITY.length : index;
};

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

const FirstTraceSetupGuide = ({
  credentialsCopied,
  getCodeBySection,
  instrumentCode,
  instrumentInstallCode,
  instrumentSampleRequestCode,
  instrumentOptions,
  apiKeysHref,
  languageTab,
  onLanguageChange,
  onInstrumentChange,
  selectedInstrument,
  selectedInstrumentLanguage,
  setupVerification,
  tabOptions,
  tabWrapperStyles,
  theme,
}) => {
  const statusLabel =
    setupVerification?.status === "ready"
      ? "Trace detected"
      : "Checking for trace";
  const selectedInstrumentLabel = selectedInstrument?.name || "your package";
  const selectedLanguageLabel =
    selectedInstrumentLanguage === "typescript" ? "TypeScript" : "Python";

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
              Connect {selectedInstrumentLabel}, then send one trace
            </Typography>
            <Typography variant="body2" color="text.secondary" maxWidth={720}>
              These snippets match {selectedInstrumentLabel} in{" "}
              {selectedLanguageLabel}. Run one request and keep this page open;
              we move you to trace review when it arrives, then guide you to
              create the first evaluator.
            </Typography>
          </Stack>

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
        </Stack>

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

        <Box
          data-testid="observe-first-trace-steps"
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "1fr",
              md: "repeat(2, minmax(0, 1fr))",
              xl: "repeat(4, minmax(0, 1fr))",
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
                2. Load keys and register project
              </Typography>
              <Button
                size="small"
                variant="outlined"
                component={RouterLink}
                href={apiKeysHref}
                startIcon={<Iconify icon="mdi:key-outline" width={16} />}
                sx={{ alignSelf: { xs: "stretch", sm: "flex-start" } }}
              >
                {credentialsCopied ? "Create another key" : "Create API key"}
              </Button>
            </Stack>
            {credentialsCopied ? (
              <Alert severity="success" icon={false} sx={{ py: 0.5 }}>
                <Typography variant="caption">
                  Credentials copied. Paste both values into the snippet, then
                  run one request.
                </Typography>
              </Alert>
            ) : (
              <Typography variant="caption" color="text.secondary">
                Create a Future AGI API key and secret key before running the
                snippet.
              </Typography>
            )}
            <InstructionCodeCopy
              ariaLabel="Copy project keys"
              text={getCodeBySection("keys")}
              language={languageTab}
            />
            <InstructionCodeCopy
              ariaLabel="Copy project registration"
              text={getCodeBySection("projectAddCode")}
              language={languageTab}
            />
          </Stack>
          <Stack spacing={1} sx={{ minWidth: 0 }}>
            <Typography variant="subtitle2">
              3. Instrument {selectedInstrumentLabel}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Add this before importing or creating the client, then run one
              request in your app.
            </Typography>
            <InstructionCodeCopy
              ariaLabel="Copy package instrumentation"
              text={instrumentCode}
              language={selectedInstrumentLanguage}
            />
          </Stack>
          <Stack spacing={1} sx={{ minWidth: 0 }}>
            <Typography variant="subtitle2">
              4. Run one {selectedInstrumentLabel} request
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Use this smoke test if you do not have a local request ready. The
              next screen waits for the trace, opens it, then points you to eval
              setup.
            </Typography>
            <InstructionCodeCopy
              ariaLabel="Copy package smoke test"
              text={instrumentSampleRequestCode}
              language={selectedInstrumentLanguage}
            />
          </Stack>
        </Box>

        <VerificationAlert setupVerification={setupVerification} />
      </Stack>
    </Box>
  );
};

FirstTraceSetupGuide.propTypes = {
  credentialsCopied: PropTypes.bool,
  getCodeBySection: PropTypes.func.isRequired,
  instrumentCode: PropTypes.string.isRequired,
  instrumentInstallCode: PropTypes.string.isRequired,
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
    if (typeof code !== "string") return "Code not available";
    return code.replace(/^\n+/, "").replace(/\n+$/, "");
  };

  // Helper functions to get the correct code based on active tabs
  const getCodeBySection = (section) => {
    const languageKey = languageTab === "python" ? "Python" : "TypeScript";
    const sectionData =
      keysData?.[section] || keysData?.[CODE_SECTION_ALIASES[section]];
    return cleanCode(sectionData?.[languageKey]);
  };

  const instrumentOptions = useMemo(
    () =>
      Object.entries(keysData?.instruments || {})
        .map(([id, instrument]) => ({
          id,
          ...instrument,
        }))
        .filter((instrument) => availableInstrumentLanguages(instrument).length)
        .sort((left, right) => {
          const rankDiff =
            instrumentSortRank(left.id) - instrumentSortRank(right.id);
          if (rankDiff !== 0) return rankDiff;
          return String(left.name).localeCompare(String(right.name));
        }),
    [keysData?.instruments],
  );
  const selectedInstrument = useMemo(() => {
    if (!instrumentOptions.length) return null;
    return (
      instrumentOptions.find(
        (instrument) => instrument.id === selectedInstrumentId,
      ) || instrumentOptions[0]
    );
  }, [instrumentOptions, selectedInstrumentId]);
  const selectedInstrumentLanguage = instrumentSupportsLanguage(
    selectedInstrument,
    languageTab,
  )
    ? languageTab
    : firstInstrumentLanguage(selectedInstrument);
  const selectedInstrumentLanguageKey = languageDataKey(
    selectedInstrumentLanguage,
  );
  const instrumentCode = cleanCode(
    selectedInstrument?.[selectedInstrumentLanguageKey]?.code ||
      getCodeBySection("projectAddCode"),
  );
  const instrumentInstallCode =
    INSTRUMENT_INSTALL_COMMANDS[selectedInstrumentLanguage]?.[
      selectedInstrument?.id
    ] || getCodeBySection("installationGuide");
  const instrumentSampleRequestCode = cleanCode(
    selectedInstrument?.[selectedInstrumentLanguageKey]?.sample_request_code ||
      defaultSampleRequestCode({
        instrumentName: selectedInstrument?.name,
        language: selectedInstrumentLanguage,
      }),
  );
  const apiKeysHref = apiKeysOnboardingHref({
    instrumentId: selectedInstrument?.id,
    language: selectedInstrumentLanguage,
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
    if (!currentOption) {
      setSelectedInstrumentId(instrumentOptions[0].id);
    }
  }, [instrumentOptions, requestedInstrumentId, selectedInstrumentId]);

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
              credentialsCopied={credentialsCopied}
              getCodeBySection={getCodeBySection}
              instrumentCode={instrumentCode}
              instrumentInstallCode={instrumentInstallCode}
              instrumentSampleRequestCode={instrumentSampleRequestCode}
              instrumentOptions={instrumentOptions}
              apiKeysHref={apiKeysHref}
              languageTab={languageTab}
              onLanguageChange={handleLanguageChange}
              onInstrumentChange={handleInstrumentChange}
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

              {/* Telemetry Section with its own tab control */}
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
                <InstructionTitle
                  title="Setup Telemetry"
                  description="Register your application to send traces to this project. The code should be added BEFORE any code execution."
                />

                {/* Separate tab selector for telemetry */}
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
                  ariaLabel="Copy telemetry setup"
                  text={getCodeBySection("projectAddCode")}
                  language={languageTab}
                  // onCopy={() => trackEvent(Events.setupTelemetryCopied)}
                />
              </Box>

              {/* Instruments Section */}
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
                <InstructionTitle
                  title="Setup Instrumentation"
                  description="Add tracing instrumentation to give you observability into your application."
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

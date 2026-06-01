import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import {
  CurrentStepGuide,
  ObserveJourneyProgress,
  ObservePanelActions,
  ObservePanelHeader,
} from "./observe-panel-utils";
import { observeFallbackJourneyPlan } from "./observe-fallback-journey-plan";
import { journeyCurrentStep } from "./journey-guide-utils";
import { persistObserveSetupIntent } from "src/sections/projects/observeOnboardingRoute";

const OBSERVE_PACKAGE_OPTIONS = [
  { id: "openai", label: "OpenAI", languages: ["python", "typescript"] },
  { id: "anthropic", label: "Anthropic", languages: ["python", "typescript"] },
  { id: "langchain", label: "LangChain", languages: ["python"] },
  {
    id: "openai_agents",
    label: "OpenAI Agents",
    languages: ["python"],
  },
  { id: "llamaindex", label: "LlamaIndex", languages: ["python"] },
  { id: "bedrock", label: "Bedrock", languages: ["python"] },
  { id: "mcp", label: "MCP", languages: ["python"] },
];

const LANGUAGE_OPTIONS = [
  { id: "python", label: "Python" },
  { id: "typescript", label: "TypeScript" },
];

const PROVIDER_ALIASES = {
  "llama-index": "llamaindex",
  llama_index: "llamaindex",
  "openai-agents": "openai_agents",
  openaiagents: "openai_agents",
};

const normalizeSetupValue = (value) =>
  typeof value === "string" ? value.trim().toLowerCase() : "";

const normalizeProvider = (provider) => {
  const normalizedValue = normalizeSetupValue(provider);
  const canonicalValue = PROVIDER_ALIASES[normalizedValue] || normalizedValue;
  return OBSERVE_PACKAGE_OPTIONS.some((option) => option.id === canonicalValue)
    ? canonicalValue
    : null;
};

const normalizeLanguage = ({ language, provider }) => {
  const selectedPackage =
    OBSERVE_PACKAGE_OPTIONS.find((option) => option.id === provider) || null;
  const normalizedLanguage = normalizeSetupValue(language);
  if (!selectedPackage) {
    return LANGUAGE_OPTIONS.some((option) => option.id === normalizedLanguage)
      ? normalizedLanguage
      : "python";
  }
  return selectedPackage.languages.includes(normalizedLanguage)
    ? normalizedLanguage
    : selectedPackage.languages[0];
};

const packageSetupLabel = ({ language, provider }) => {
  const selectedPackage = OBSERVE_PACKAGE_OPTIONS.find(
    (option) => option.id === provider,
  );
  if (!selectedPackage) return "";
  const languageLabel =
    LANGUAGE_OPTIONS.find((option) => option.id === language)?.label ||
    "Python";
  return `${selectedPackage.label} ${languageLabel}`;
};

const hrefWithObservePackage = (href, { language, provider } = {}) => {
  if (!href || !href.startsWith("/") || href.startsWith("//")) return href;

  const [withoutHash, hash] = href.split("#");
  const [pathname, query = ""] = withoutHash.split("?");
  const params = new URLSearchParams(query);
  if (provider) params.set("provider", provider);
  if (language) params.set("language", language);
  const queryString = params.toString();
  return `${pathname}${queryString ? `?${queryString}` : ""}${
    hash ? `#${hash}` : ""
  }`;
};

function ObservePackagePicker({
  language,
  onLanguageChange,
  onProviderChange,
  provider,
}) {
  const selectedPackage =
    OBSERVE_PACKAGE_OPTIONS.find((option) => option.id === provider) || null;

  return (
    <Box
      data-testid="observe-package-picker"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 1.5,
      }}
    >
      <Stack spacing={1}>
        <Stack spacing={0.25}>
          <Typography variant="subtitle2">
            Which package sends model calls?
          </Typography>
          <Typography variant="body2" color="text.secondary">
            This decides the install command, package setup code, request
            example, and trace checks on the setup page.
          </Typography>
        </Stack>
        {!provider ? (
          <Alert severity="info" sx={{ borderRadius: 1 }}>
            Select the SDK package before opening setup.
          </Alert>
        ) : null}
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          {OBSERVE_PACKAGE_OPTIONS.map((option) => (
            <Button
              key={option.id}
              size="small"
              variant={option.id === provider ? "contained" : "outlined"}
              color={option.id === provider ? "primary" : "inherit"}
              onClick={() => {
                onProviderChange(option.id);
                if (!option.languages.includes(language)) {
                  onLanguageChange(option.languages[0]);
                }
              }}
              sx={{ textTransform: "none" }}
            >
              {option.label}
            </Button>
          ))}
        </Stack>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          {LANGUAGE_OPTIONS.map((option) => {
            const isDisabled = selectedPackage
              ? !selectedPackage.languages.includes(option.id)
              : false;
            return (
              <Button
                key={option.id}
                size="small"
                variant={option.id === language ? "contained" : "outlined"}
                color={option.id === language ? "primary" : "inherit"}
                disabled={isDisabled}
                onClick={() => onLanguageChange(option.id)}
                sx={{ textTransform: "none" }}
              >
                {option.label}
              </Button>
            );
          })}
        </Stack>
      </Stack>
    </Box>
  );
}

ObservePackagePicker.propTypes = {
  language: PropTypes.string.isRequired,
  onLanguageChange: PropTypes.func.isRequired,
  onProviderChange: PropTypes.func.isRequired,
  provider: PropTypes.string,
};

export default function ObserveSetupPanel({
  action,
  fallbackAction,
  initialLanguage,
  initialProvider,
  journeyPlan,
  onPrimaryClick,
  onFallbackClick,
  onCheckAgain,
  isChecking = false,
  singleActionFocus = false,
  stage = "connect_observability",
}) {
  const normalizedInitialProvider = normalizeProvider(initialProvider);
  const normalizedInitialLanguage = normalizeLanguage({
    language: initialLanguage,
    provider: normalizedInitialProvider,
  });
  const [selectedProvider, setSelectedProvider] = useState(
    normalizedInitialProvider,
  );
  const [selectedLanguage, setSelectedLanguage] = useState(
    normalizedInitialLanguage,
  );
  const effectiveJourneyPlan = journeyPlan || observeFallbackJourneyPlan(stage);
  const currentStep = journeyCurrentStep(effectiveJourneyPlan, stage);
  const steps = effectiveJourneyPlan?.steps || [];
  const currentStepIndex = Math.max(steps.indexOf(currentStep), 0);
  const nextStep = steps[currentStepIndex + 1] || null;
  const actionStep = currentStep || {
    stage,
    label: action?.title || "Connect your agent",
    description:
      action?.description ||
      "Choose the package your app uses, create an Observe project, and send one trace.",
    tourAnchor: "observe_create_project_button",
  };
  const shouldShowPackagePicker = stage === "connect_observability";
  const selectedSetupLabel = selectedProvider
    ? packageSetupLabel({
        language: selectedLanguage,
        provider: selectedProvider,
      })
    : "";
  const packageAwareStep =
    shouldShowPackagePicker && selectedSetupLabel
      ? {
          ...actionStep,
          label: `Open ${selectedSetupLabel} setup`,
          description: `The next page shows the ${selectedSetupLabel} install command, Future AGI key setup, provider key setup, package setup code, and a ready-to-run request. Keep it open after the request; Future AGI waits for the first trace, then guides trace review and evaluator setup.`,
        }
      : shouldShowPackagePicker
        ? {
            ...actionStep,
            label: "Choose SDK package",
            description:
              "Select the SDK package that sends your model call. The next page will show matching install code, setup code, a request example, trace wait, trace review, and evaluator setup.",
          }
        : actionStep;

  useEffect(() => {
    setSelectedProvider(normalizedInitialProvider);
    setSelectedLanguage(normalizedInitialLanguage);
  }, [normalizedInitialLanguage, normalizedInitialProvider]);

  useEffect(() => {
    if (!shouldShowPackagePicker) return;
    persistObserveSetupIntent({
      setupLanguage: selectedLanguage,
      setupProvider: selectedProvider,
    });
  }, [selectedLanguage, selectedProvider, shouldShowPackagePicker]);

  const packageAwareAction = useMemo(() => {
    if (!shouldShowPackagePicker || !action?.href) return action;
    if (!selectedProvider) {
      return {
        ...action,
        blocked: true,
        blockedReason: "package_required",
        ctaLabel: "Choose package to continue",
        description:
          "Select the SDK package your app uses before opening setup.",
        href: null,
        routeAvailable: false,
        title: "Choose SDK package",
      };
    }
    return {
      ...action,
      ctaLabel: `Open ${selectedSetupLabel} setup`,
      description: `Open setup with ${selectedSetupLabel} install, provider key setup, package setup code, a ready-to-run request, and trace checks.`,
      href: hrefWithObservePackage(action.href, {
        language: selectedLanguage,
        provider: selectedProvider,
      }),
      title: `Open ${selectedSetupLabel} setup`,
    };
  }, [
    action,
    selectedLanguage,
    selectedProvider,
    selectedSetupLabel,
    shouldShowPackagePicker,
  ]);
  const actionSlot = (
    <ObservePanelActions
      action={packageAwareAction}
      fallbackAction={fallbackAction}
      onPrimaryClick={onPrimaryClick}
      onFallbackClick={onFallbackClick}
      onCheckAgain={onCheckAgain}
      isChecking={isChecking}
      journeyStep={packageAwareStep}
      singleActionFocus={singleActionFocus || Boolean(actionStep)}
    />
  );

  return (
    <Box
      data-testid="observe-setup-panel"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 2,
        bgcolor: "background.paper",
      }}
    >
      <Stack spacing={2}>
        {!singleActionFocus ? (
          <ObservePanelHeader
            eyebrow={effectiveJourneyPlan.eyebrow || "Observe setup"}
            title={effectiveJourneyPlan.title || "Connect your agent"}
            description={
              effectiveJourneyPlan.description ||
              "Create the project, send one trace, then return here for the first review."
            }
            chips={effectiveJourneyPlan.chips || ["observe", "setup"]}
          />
        ) : null}
        {shouldShowPackagePicker ? (
          <ObservePackagePicker
            language={selectedLanguage}
            onLanguageChange={setSelectedLanguage}
            onProviderChange={setSelectedProvider}
            provider={selectedProvider}
          />
        ) : null}
        {shouldShowPackagePicker && selectedSetupLabel ? (
          <Alert
            severity="success"
            data-testid="observe-selected-package-handoff"
            sx={{ alignItems: "flex-start", borderRadius: 1 }}
          >
            <Stack spacing={0.25}>
              <Typography variant="subtitle2">
                {selectedSetupLabel} path selected
              </Typography>
              <Typography variant="body2">
                Setup opens with package-specific code and stays in the same
                loop: copy code, run one request, wait for trace, review it,
                then create an evaluator.
              </Typography>
            </Stack>
          </Alert>
        ) : null}
        {actionStep ? (
          <CurrentStepGuide
            actionSlot={actionSlot}
            label={singleActionFocus ? "Current step" : "Start here"}
            nextStep={nextStep}
            step={packageAwareStep}
            stage={stage}
            stepNumber={currentStepIndex + 1}
            totalSteps={steps.length || 1}
          />
        ) : null}
        <ObserveJourneyProgress
          journeyPlan={effectiveJourneyPlan}
          singleActionFocus={singleActionFocus}
          showCurrentStepGuide={false}
          stage={stage}
        />
        {!actionStep ? (
          <ObservePanelActions
            action={action}
            fallbackAction={fallbackAction}
            onPrimaryClick={onPrimaryClick}
            onFallbackClick={onFallbackClick}
            onCheckAgain={onCheckAgain}
            isChecking={isChecking}
            journeyStep={actionStep}
            singleActionFocus={singleActionFocus}
          />
        ) : null}
      </Stack>
    </Box>
  );
}

ObserveSetupPanel.propTypes = {
  action: PropTypes.object,
  fallbackAction: PropTypes.object,
  initialLanguage: PropTypes.string,
  initialProvider: PropTypes.string,
  isChecking: PropTypes.bool,
  journeyPlan: PropTypes.object,
  onCheckAgain: PropTypes.func,
  onFallbackClick: PropTypes.func,
  onPrimaryClick: PropTypes.func,
  singleActionFocus: PropTypes.bool,
  stage: PropTypes.string,
};

import { classicExperimentEvalSelection } from "src/launch/experimentExecutionPin";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  IconButton,
  Slider,
  TextField,
  Typography,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "notistack";
import { useFeatureLocked, CAPABILITY } from "src/hooks/useCapabilities";
import { useErrorLocalizationAvailable } from "src/hooks/useErrorLocalization";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { FAGI_MODEL_VALUES } from "src/sections/evals/components/ModelSelector";
import { DecisionJudgesContext } from "src/sections/common/DecisionJudges/DecisionJudgesContext";
import DecisionQuestionEditor from "src/sections/common/DecisionJudges/DecisionQuestionEditor";
import {
  decisionConfigErrors,
  decisionModelOf,
  defaultDecisionConfig,
  outputKindOf,
  questionTypeOf,
  withModel,
  withoutInstructions,
} from "src/sections/common/DecisionJudges/decisionModels";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show/ShowComponent";
import ResizablePanels from "src/components/resizablePanels/ResizablePanels";
import TaskFilterBar from "src/sections/tasks/components/TaskFilterBar";
import { buildApiFilterArray } from "src/sections/tasks/components/TaskLivePreview";
import { ROW_TYPE_LABELS } from "src/utils/constants";
import {
  useEvalDetail,
  useUpdateEval,
} from "src/sections/evals/hooks/useEvalDetail";
import {
  useEvalVersions,
  useCreateEvalVersion,
} from "src/sections/evals/hooks/useEvalVersions";
import InstructionEditor from "src/sections/evals/components/InstructionEditor";
import LLMPromptEditor from "src/sections/evals/components/LLMPromptEditor";
import CodeEvalEditor from "src/sections/evals/components/CodeEvalEditor";
import OutputTypeConfig from "src/sections/evals/components/OutputTypeConfig";
import FewShotExamples from "src/sections/evals/components/FewShotExamples";
import { useCompositeDetail } from "src/sections/evals/hooks/useCompositeEval";
import { useCompositeChildrenUnionKeys } from "src/sections/evals/hooks/useCompositeChildrenKeys";
import DatasetTestMode from "src/sections/evals/components/DatasetTestMode";
import TracingTestMode from "src/sections/evals/components/TracingTestMode";
import SimulationTestMode from "src/sections/evals/components/SimulationTestMode";
import CreateSimulationPreviewMode from "src/sections/evals/components/CreateSimulationPreviewMode";
import TestPlayground from "src/sections/evals/components/TestPlayground";
import VersionBadge from "src/sections/evals/components/VersionBadge";
import { RECORDED_VERSION_TOOLTIP } from "src/sections/evals/Helpers/evalVersionCopy";
import EvalTypeBadge from "src/sections/evals/components/EvalTypeBadge";
import {
  isTypedEvalType,
  typedOwnJudgeOf,
} from "src/sections/common/EvalTypes/typedEvalTypes";
import TypedEvalBindingConfig from "src/sections/common/EvalTypes/TypedEvalBindingConfig";
import { useEvalPickerContext } from "./context/EvalPickerContext";
import CompositeChildParams from "./CompositeChildParams";
import {
  getEvalCode,
  getEvalCodeLanguage,
  getEvalConfigParamsDesc,
  getEvalFunctionParamsSchema,
  getEvalRequiredKeys,
  normalizeEvalPickerEval,
} from "./evalPickerValue";
import { getEvalBaseName } from "src/sections/common/EvaluationDrawer/common";
import {
  canonicalEntries,
  extractVariablesFromMessages,
} from "src/utils/utils";
import { format } from "date-fns";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import {
  buildEvalTemplateConfig,
  buildCompositeSourceModeProps,
  buildDataInjection,
  contextOptionsForRowType,
  extractCodeEvaluateParams,
  getSourceModeVariables,
  hasNonEmptyPromptMessage,
} from "./evalPickerConfigUtils";
import RequiredMark from "src/components/RequiredMark";
import AllowNaSwitch from "./AllowNaSwitch";
import {
  bindingContextOptions,
  bindingErrorLocalizerOf,
  bindingRunConfigOf,
  buildBindingSavePayload,
  buildBindingVersionConfig,
  reconcileBindingSave,
  storedBindingPayload,
} from "./bindingSavePayload";
import useJuryDatasetInputs, {
  withJuryInputs,
} from "src/sections/evals/jury/useJuryDatasetInputs";
import {
  allowNaApplies,
  stripLaunchAllowNa,
  versionPinOf,
} from "./allowNaModel";

const ERROR_LOCALIZER_LOCKED_TOOLTIP =
  "Error Localization isn't enabled for this workspace.";

const build_tools_payload = (selected_tools) =>
  (selected_tools || []).reduce((acc, tool_name) => {
    if (tool_name) acc[tool_name] = true;
    return acc;
  }, {});

// ── Main Component ──

const SOURCE_LABELS = {
  dataset: "Dataset",
  experiment: "Dataset",
  tracing: "Tracing",
  simulation: "Simulation",
  task: "Task",
  custom: "Custom",
  composite: "Composite",
};

const SOURCE_NAME_SLUGS = {
  dataset: "dataset",
  experiment: "experiment",
  "run-experiment": "experiment",
  optimization: "optimization",
  "run-optimization": "optimization",
  workbench: "workbench",
  simulation: "simulation",
  "create-simulate": "simulation",
  task: "task",
  composite: "composite",
};

// Hosts whose drawer saves an edited binding through edit_and_run_user_eval
// (EvaluationDrawer's edit branch). There a present pinned_version_id is a
// pure version selection, so an edited user-eval definition must leave
// versionId unset to keep the create-version update.
const CREATE_VERSION_EDIT_HOSTS = new Set([
  "dataset",
  "run-optimization",
  "experiment",
]);

const getEvalPromptText = (evalData, config = {}) =>
  evalData?.instructions || config?.rule_prompt || "";

// The judge model a saved binding names (edit mode), in its full form: a
// bare Turing suffix ("small") the eval list sometimes returns maps back to
// its canonical value, so the chip and the save keep the same model.
const savedBindingModelOf = (evalData) => {
  const m = evalData?.model ?? evalData?.selectedModel ?? null;
  if (!m || typeof m !== "string") return null;
  if (FAGI_MODEL_VALUES.has(m)) return m;
  const full = [...FAGI_MODEL_VALUES].find((v) => v.endsWith(`_${m}`));
  return full ?? m;
};

// A saved decision question (the binding's config.decision, a template's
// config.jev_mapping), without the instructions an earlier draft carried.
const savedDecisionOf = (config) =>
  withoutInstructions(config?.decision ?? config?.jev_mapping ?? null);

// The pass bar a saved binding carries in its run config. A run applies it
// before the bar of the version it runs (resolve_pass_threshold).
const savedBindingBarOf = (evalData) => {
  const runConfig =
    evalData?.run_config ||
    evalData?.runConfig ||
    evalData?.config?.run_config ||
    {};
  const bar = Number(runConfig.pass_threshold ?? runConfig.passThreshold);
  return runConfig.pass_threshold == null && runConfig.passThreshold == null
    ? null
    : Number.isFinite(bar)
      ? bar
      : null;
};

const EvalPickerConfigFull = ({
  evalData,
  onBack,
  onSave: onSaveSelection,
  isSaving,
  progress = null,
  primaryLabel = null,
  showClose = false,
}) => {
  // Fail closed while capabilities load (both flags true) so gated controls
  // never flash as available before the fetch resolves.
  const { locked: fagiLocked, isLoading: capsLoading } = useFeatureLocked(
    CAPABILITY.TURING_MODELS,
  );
  const { locked: agentEvalLocked, isLoading: agentCapsLoading } =
    useFeatureLocked(CAPABILITY.AGENTIC_EVAL);
  const { isLoading: deploymentLoading } = useDeploymentMode();
  const errorLocalizerAvailable = useErrorLocalizationAvailable();
  // Confirmed denial (loaded AND not allowed) — seed the model default raw and
  // only strip it here, never off `locked` (true while loading), so entitled
  // users keep "turing_large" through the fetch.
  const fagiModelsDenied = fagiLocked && !capsLoading;
  const {
    source,
    sourceId,
    sourceColumns,
    extraColumns,
    sourceRowType,
    sourcePreviewData,
    isEditMode,
    requiredColumnId,
    onFiltersChange,
    sourceTimeWindow,
    filterForm: localFilterForm,
    onClose,
    requireInputs,
  } = useEvalPickerContext();
  const bindingEdit = isEditMode && CREATE_VERSION_EDIT_HOSTS.has(source);
  const normalizedEvalData = useMemo(
    () => normalizeEvalPickerEval(evalData),
    [evalData],
  );
  const onSave = (payload) =>
    onSaveSelection(classicExperimentEvalSelection(payload, source));
  const templateId =
    evalData?.templateId || evalData?.template_id || evalData?.id;
  // ── Data (same hooks as EvalDetailPage) ──
  const {
    data: fullEval,
    isLoading,
    isError,
    refetch: refetchTemplate,
  } = useEvalDetail(templateId);
  const normalizedFullEval = useMemo(
    () => normalizeEvalPickerEval(fullEval),
    [fullEval],
  );
  const {
    data: versionsData,
    isLoading: versionsLoading,
    isError: versionsError,
    refetch: refetchVersions,
  } = useEvalVersions(templateId);
  const versions = useMemo(() => versionsData?.versions || [], [versionsData]);
  const updateEval = useUpdateEval(templateId);
  const createVersion = useCreateEvalVersion(templateId);

  // ── Editable state (mirrors EvalDetailPage) ──
  const [selectedVersionId, setSelectedVersionId] = useState(
    evalData?.pinned_version_id ?? evalData?.pinnedVersionId ?? null,
  );
  const [instructions, setInstructions] = useState("");
  const [code, setCode] = useState("");
  const [codeLanguage, setCodeLanguage] = useState("python");
  const [model, setModel] = useState("turing_large");
  // Drop the seeded Turing default only once denial is confirmed, so entitled
  // users keep it through the capabilities fetch (and any config load).
  useEffect(() => {
    if (fagiModelsDenied && FAGI_MODEL_VALUES.has(model)) setModel("");
  }, [fagiModelsDenied, model]);
  const [openModelMenuSignal, setOpenModelMenuSignal] = useState(0);
  const [outputType, setOutputType] = useState("pass_fail");
  const [passThreshold, setPassThreshold] = useState(0.5);
  // W1-4: the judge may answer N/A on this eval (config.allow_na), seeded
  // from the card's saved config (a reopened card hands it over as
  // run_config), else the binding's, else the template's; the populate
  // effects below re-seed it once the template's detail arrives.
  const [allowNa, setAllowNa] = useState(
    Boolean(
      evalData?.run_config?.allow_na ??
        evalData?.bindingConfig?.allow_na ??
        evalData?.config?.allow_na ??
        fullEval?.config?.allow_na,
    ),
  );
  const [choiceScores, setChoiceScores] = useState({});
  // W1: a decision-model judge's question (model, format, labels, levels).
  const [decision, setDecision] = useState(null);
  const [multiChoice, setMultiChoice] = useState(false);
  const [messages, setMessages] = useState([{ role: "system", content: "" }]);
  const [fewShotExamples, setFewShotExamples] = useState([]);
  const [templateFormat, setTemplateFormat] = useState("mustache");
  // Runtime-override state surfaced via ModelSelector's + button. These
  // flow through to the `run_config` block of the save payload and become
  // per-dataset-attachment overrides on the UserEvalMetric.
  const [agentMode, setAgentMode] = useState("agent");
  const [useInternet, setUseInternet] = useState(false);
  const [summaryType, setSummaryType] = useState("concise");
  const [connectorIds, setConnectorIds] = useState([]);
  const [knowledgeBaseIds, setKnowledgeBaseIds] = useState([]);
  const [contextOptions, setContextOptions] = useState(["variables_only"]);
  const [errorLocalizerEnabled, setErrorLocalizerEnabled] = useState(false);
  const errorLocalizerActive =
    errorLocalizerEnabled && !agentEvalLocked && errorLocalizerAvailable;
  // Name for the UserEvalMetric — defaults to template name, user can customise
  const [evalName, setEvalName] = useState("");
  const [dataReady, setDataReady] = useState(false);
  const [hydratedVersionId, setHydratedVersionId] = useState(undefined);
  const [isDirty, setIsDirty] = useState(false);
  // W1-3: a reopened card keeps its version pin ("latest" or "v3", saved
  // as config.version_pin), selected once the template's versions load.
  const savedPin = evalData?.run_config?.version_pin ?? null;
  const versionSelectionChanged = useRef(false);
  useEffect(() => {
    const byPin =
      savedPin === "latest"
        ? "latest"
        : savedPin
          ? versions.find((v) => `v${v.version_number}` === savedPin)?.id ??
            null
          : null;
    const pinned = Object.prototype.hasOwnProperty.call(
      evalData || {},
      "pinned_version_id",
    )
      ? evalData.pinned_version_id
      : Object.prototype.hasOwnProperty.call(evalData || {}, "pinnedVersionId")
        ? evalData.pinnedVersionId
        : byPin;
    if (
      pinned &&
      !selectedVersionId &&
      !isDirty &&
      !versionSelectionChanged.current
    ) {
      setSelectedVersionId(pinned);
    }
  }, [
    evalData?.pinned_version_id,
    evalData?.pinnedVersionId,
    savedPin,
    versions,
    selectedVersionId,
    isDirty,
  ]);
  const [isTesting, setIsTesting] = useState(false);
  const [testPassed, setTestPassed] = useState(false);
  const [testError, setTestError] = useState(null);
  const [sourceReady, setSourceReady] = useState(false);
  const [promptMessageError, setPromptMessageError] = useState("");
  const [sourceMapping, setSourceMapping] = useState(
    () => evalData?.mapping || {},
  );
  const sourceRef = useRef(null);
  const locallySavedVersion = useRef(null);
  const [initialBindingDraft, setInitialBindingDraft] = useState(null);
  const [storedVersionConfig, setStoredVersionConfig] = useState(null);
  const [savedBindingPayload, setSavedBindingPayload] = useState(null);
  const [savedVersionBaseline, setSavedVersionBaseline] = useState(null);
  const [baselineCandidate, setBaselineCandidate] = useState(null);
  const [hydrationInputs, setHydrationInputs] = useState(null);
  const hydrationReady =
    Boolean(fullEval) &&
    !isLoading &&
    !capsLoading &&
    !agentCapsLoading &&
    !deploymentLoading;
  const hydrationAccess = `${fagiLocked}:${agentEvalLocked}:${errorLocalizerAvailable}`;
  const storedPinReference = Object.hasOwn(evalData || {}, "pinned_version_id")
    ? evalData.pinned_version_id
    : Object.hasOwn(evalData || {}, "pinnedVersionId")
      ? evalData.pinnedVersionId
      : savedPin;
  const selectedVersion =
    selectedVersionId === locallySavedVersion.current?.id
      ? locallySavedVersion.current
      : selectedVersionId === "latest"
        ? versions[0]
        : versions.find((v) => v.id === selectedVersionId);
  const versionReady =
    Boolean(versionsData) &&
    !versionsLoading &&
    (!storedPinReference || Boolean(selectedVersionId && selectedVersion));
  const selectedBaseConfig = useMemo(
    () =>
      selectedVersion
        ? selectedVersion.config_snapshot ||
          selectedVersion.configSnapshot ||
          {}
        : fullEval?.config || evalData?.config || {},
    [selectedVersion, fullEval, evalData],
  );

  const localFormFilters = useWatch({
    control: localFilterForm.control,
    name: "filters",
  });
  const localApiFilters = useMemo(
    () =>
      buildApiFilterArray(
        localFormFilters,
        sourceTimeWindow?.startDate,
        sourceTimeWindow?.endDate,
      ),
    [localFormFilters, sourceTimeWindow?.startDate, sourceTimeWindow?.endDate],
  );

  const handleSourceReadyChange = useCallback((ready, mapping) => {
    setSourceReady(ready);
    if (mapping) setSourceMapping(mapping);
  }, []);

  // Dataset columns for autocomplete in editors
  const [datasetColumns, setDatasetColumns] = useState([]);
  const [datasetJsonSchemas, setDatasetJsonSchemas] = useState({});
  const [codeParams, setCodeParams] = useState({});
  const handleColumnsLoaded = useCallback((cols, jsonSchemas) => {
    setDatasetColumns(cols || []);
    setDatasetJsonSchemas(jsonSchemas || {});
  }, []);

  const functionParamsSchema = getEvalFunctionParamsSchema(normalizedFullEval);
  const configParamsDesc = getEvalConfigParamsDesc(normalizedFullEval);

  const handleCodeParamChange = useCallback((key, value) => {
    setCodeParams((prev) => ({ ...prev, [key]: value }));
  }, []);

  // Derived
  const evalType =
    normalizedFullEval?.evalType || normalizedEvalData?.evalType || "llm";
  const isSystemEval = normalizedFullEval?.owner === "system";
  const isComposite =
    (normalizedFullEval?.templateType || normalizedEvalData?.templateType) ===
    "composite";
  // Composite metadata + per-binding weight overrides. Loaded lazily via
  // `useCompositeDetail` only when the selected template is composite so
  // single-eval picks don't pay the round-trip cost. `compositeChildWeights`
  // is the state that flows into `composite_weight_overrides` on save.
  const {
    data: compositeDetail,
    isError: compositeError,
    refetch: refetchComposite,
  } = useCompositeDetail(templateId, isComposite);
  const compositeUnionKeys = useCompositeChildrenUnionKeys(
    compositeDetail?.children || [],
  );
  const [compositeChildWeights, setCompositeChildWeights] = useState({});
  const [compositeReady, setCompositeReady] = useState(false);
  const compositePopulatedRef = useRef(false);
  useEffect(() => {
    if (!isComposite) return;
    if (!compositeDetail) return;
    // In edit mode, wait for evalData so saved overrides aren't missed
    if (isEditMode && !evalData) return;
    if (compositePopulatedRef.current) return;
    // Start from template child weights, then overlay saved per-binding overrides
    const initial = {};
    (compositeDetail.children || []).forEach((c) => {
      if (c?.child_id != null) {
        initial[c.child_id] = c.weight != null ? c.weight : 1.0;
      }
    });
    const saved = evalData?.composite_weight_overrides || {};
    if (saved && typeof saved === "object") {
      Object.entries(saved).forEach(([childId, w]) => {
        if (w != null) initial[childId] = w;
      });
    }
    setCompositeChildWeights(initial);
    setCompositeReady(true);
    compositePopulatedRef.current = true;
  }, [isComposite, compositeDetail, evalData]);
  // Configuring a per-dataset attachment (UserEvalMetric) — NOT editing the
  // underlying template itself. Lock-down policy:
  //   - System evals: instructions + output_type category are READ-ONLY.
  //                   Model / + menu / scoring settings (within the type) are editable
  //                   and saved as runtime overrides in config.run_config.
  //   - User evals:   full editing, BUT the output_type category is also locked
  //                   (you can't switch pass_fail → scoring after template creation
  //                   — that's a versioning concern). Scoring settings still editable.
  const isInstructionsReadOnly = isSystemEval;
  const isOutputTypeCategoryLocked = true; // locked for both system and user
  const currentVersion =
    normalizedFullEval?.currentVersion ||
    normalizedEvalData?.currentVersion ||
    "V1";
  const selectedVersionNumber =
    selectedVersion?.version_number ?? selectedVersion?.versionNumber ?? null;
  const shownVersion = selectedVersionNumber
    ? `V${selectedVersionNumber}`
    : currentVersion;

  // Extract variables for the mapping panel. Priority:
  //   1. Composite: union of each child's required_keys (backend populates
  //      these on `CompositeDetailResponse.children[].required_keys`).
  //   2. required_keys on the template (authoritative for system evals)
  //   3. `{{var}}` patterns in the user-edited instructions
  const variables = useMemo(() => {
    if (isComposite) {
      const union = new Set();
      (compositeDetail?.children || []).forEach((child) => {
        (child?.required_keys || []).forEach((k) => union.add(k));
      });
      return [...union];
    }
    const requiredKeys =
      getEvalRequiredKeys(normalizedFullEval) ||
      getEvalRequiredKeys(normalizedEvalData) ||
      [];

    if (evalType === "code") {
      const savedMapping = normalizedEvalData?.mapping || {};
      const savedStdvars = ["input", "output", "expected"].filter(
        (v) => v in savedMapping,
      );

      // System code evals always have the canonical
      // `evaluate(input, output, expected, context, **kwargs)` signature —
      // the real keys live in required_keys, so trust them directly and
      // never live-parse (would surface input/output/expected/context).
      if (isSystemEval) {
        if (requiredKeys.length > 0) return [...new Set(requiredKeys)];
        return savedStdvars;
      }

      // User-authored code: live-parse the `def evaluate(...)` signature
      // so adding / renaming a param immediately surfaces a mapping row.
      // Fall back to saved mapping + template required_keys when the code
      // can't be parsed (non-python language, no `def evaluate`).
      const liveParams = extractCodeEvaluateParams(code, codeLanguage);
      if (liveParams.length > 0) {
        return [...new Set([...liveParams, ...requiredKeys])];
      }
      return [...new Set([...savedStdvars, ...requiredKeys])];
    }

    // System evals + Jinja mode: use static required_keys.
    // Jinja's extractJinjaVariables() only returns root names (e.g. "file")
    // not full paths (e.g. "file.code"), so requiredKeys is authoritative.
    if (
      (isSystemEval || templateFormat === "jinja") &&
      requiredKeys.length > 0
    ) {
      return [...new Set(requiredKeys)];
    }

    // User evals (mustache): prefer live extraction so mapping updates as user
    // types. For LLM evals, scan every turn (System / User / Assistant), not
    // just the System-derived instructions field.
    const templateVars =
      evalType === "llm"
        ? extractVariablesFromMessages(instructions, messages, "mustache")
        : ((instructions || "").match(/\{\{\s*([^{}]+?)\s*\}\}/g) || []).map(
            (m) => m.replace(/\{\{|\}\}/g, "").trim(),
          );
    if (templateVars.length > 0) return [...new Set(templateVars)];
    // Fallback: stored required_keys (before instructions hydrate)
    if (requiredKeys.length > 0) return [...new Set(requiredKeys)];
    return [];
  }, [
    instructions,
    messages,
    normalizedFullEval,
    normalizedEvalData,
    evalType,
    isComposite,
    isSystemEval,
    compositeDetail,
    templateFormat,
    code,
    codeLanguage,
  ]);

  const hasDataInjection = useMemo(
    () =>
      evalType === "agent" &&
      (source === "task" || source === "tracing") &&
      Array.isArray(contextOptions) &&
      contextOptions.some((o) => o && o !== "variables_only"),
    [evalType, source, contextOptions],
  );

  const visibleCodeParamEntries = useMemo(() => {
    if (!functionParamsSchema) return [];
    const variableSet = new Set(Array.isArray(variables) ? variables : []);
    return canonicalEntries(functionParamsSchema).filter(
      ([key]) => !variableSet.has(key),
    );
  }, [functionParamsSchema, variables]);

  const compositeSourceModeProps = useMemo(
    () =>
      buildCompositeSourceModeProps({
        isComposite,
        fullEval,
        compositeDetail,
        compositeChildWeights,
      }),
    [isComposite, fullEval, compositeDetail, compositeChildWeights],
  );

  // shared-edit(F68): add the saved jury's structured dataset inputs.
  const baseSourceModeVariables = useMemo(
    () =>
      getSourceModeVariables({
        isComposite,
        variables,
        compositeUnionKeys,
      }),
    [isComposite, variables, compositeUnionKeys],
  );
  // hook(F68): map steps/checks through the ordinary dataset column picker.
  const juryInputs = useJuryDatasetInputs(
    templateId,
    isComposite,
    selectedVersionId,
    [],
    evalData?.bindingConfig,
    setSelectedVersionId,
    evalData?.mapping,
  );
  const sourceModeVariables = withJuryInputs(
    baseSourceModeVariables,
    juryInputs,
  );
  // shared-edit(F68): the dataset mapping keeps the complete composite union
  // (compositeDetail), so a child definition still loading never prunes a
  // saved mapping; the jury's inputs are added only when it shows.
  const datasetModeVariables = withJuryInputs(variables, juryInputs);

  const hasValidPromptMessages = useMemo(
    () => evalType !== "llm" || hasNonEmptyPromptMessage(messages),
    [evalType, messages],
  );
  useEffect(() => {
    if (
      !selectedVersionId ||
      !versions.length ||
      isDirty ||
      (bindingEdit && (!hydrationReady || initialBindingDraft)) ||
      locallySavedVersion.current?.id === selectedVersionId
    )
      return;
    // W1-3: Latest loads the newest version (the list is newest first).
    const loadId =
      selectedVersionId === "latest" ? versions[0]?.id : selectedVersionId;
    const version = versions.find((v) => v.id === loadId);
    if (!version) return;

    const config = {
      ...(version.config_snapshot || version.configSnapshot || {}),
      ...(isEditMode
        ? evalData?.bindingConfig || evalData?.binding_config || {}
        : {}),
      ...(isEditMode ? bindingRunConfigOf(evalData) : {}),
    };
    const promptText = config.rule_prompt || version.criteria || "";
    const _type =
      normalizedFullEval?.evalType || normalizedEvalData?.evalType || "llm";

    if (_type === "code") {
      setInstructions("");
      setCode(config.code || "");
    } else {
      setInstructions(promptText);
      setCode("");
    }
    if (config.messages?.length > 0) {
      setMessages(config.messages);
    } else if (_type === "llm" && promptText) {
      setMessages([{ role: "system", content: promptText }]);
    }

    setModel(
      (isEditMode ? savedBindingModelOf(evalData) : null) ||
        config.model ||
        fullEval?.model ||
        "turing_large",
    );
    setCodeLanguage(config.language || "python");
    setCodeParams(
      evalData?.params ||
        evalData?.storedBinding?.config?.params ||
        config.params ||
        {},
    );
    // W1: a version saved with a decision question brings it back.
    setDecision(savedDecisionOf(config));
    // L7c g7 (editor:output-type): a version's snapshot names its output
    // "Pass/Fail" / "score" / "choices" (as Save version writes it); the
    // radio's values are pass_fail / percentage / deterministic.
    if (config.output)
      setOutputType(
        {
          "Pass/Fail": "pass_fail",
          pass_fail: "pass_fail",
          score: "percentage",
          percentage: "percentage",
          choices: "deterministic",
          deterministic: "deterministic",
        }[config.output] ?? config.output,
      );
    // A reopened binding shows its own saved bar, the one its runs apply.
    const bar =
      (isEditMode ? savedBindingBarOf(evalData) : null) ??
      config.pass_threshold;
    if (bar != null) setPassThreshold(bar);
    if (config.choice_scores) setChoiceScores(config.choice_scores);
    if (config.multi_choice != null) setMultiChoice(!!config.multi_choice);
    if (config.template_format) setTemplateFormat(config.template_format);
    if (Array.isArray(config.few_shot_examples))
      setFewShotExamples(config.few_shot_examples);

    if (config.agent_mode) setAgentMode(config.agent_mode);
    if (config.check_internet != null) setUseInternet(!!config.check_internet);
    const summaryVal =
      typeof config.summary === "object"
        ? config.summary?.type
        : config.summary;
    if (summaryVal) setSummaryType(summaryVal);
    setConnectorIds(
      Array.isArray(config.tools)
        ? config.tools
        : Object.keys(config.tools || {}).filter((key) => config.tools[key]),
    );
    setKnowledgeBaseIds(config.knowledge_bases || []);
    setContextOptions(bindingContextOptions(config.data_injection));
    setErrorLocalizerEnabled(
      isEditMode
        ? bindingErrorLocalizerOf(evalData, config.error_localizer_enabled)
        : Boolean(config.error_localizer_enabled),
    );
    // W1-4: the card's own N/A switch, else the version's.
    setAllowNa(
      Boolean(
        evalData?.run_config?.allow_na ??
          evalData?.bindingConfig?.allow_na ??
          config.allow_na ??
          fullEval?.config?.allow_na,
      ),
    );

    if (isEditMode) setEvalName(evalData?.name || fullEval?.name || "");
    setIsDirty(false);
    setDataReady(true);
    setHydratedVersionId(selectedVersionId);
    setHydrationInputs({ detail: fullEval, access: hydrationAccess });
    initialLoadDone.current = true;
  }, [
    bindingEdit,
    hydrationReady,
    hydrationAccess,
    initialBindingDraft,
    selectedVersionId,
    versions,
    isDirty,
    fullEval,
    normalizedFullEval,
    normalizedEvalData,
    isEditMode,
    evalData,
  ]);

  // ── Populate from eval detail (same logic as EvalDetailPage) ──
  const initialLoadDone = useRef(false);
  useEffect(() => {
    // React Query can return cached detail first and fresh network detail next.
    // Re-hydrate while the form is still clean so fresh fields (e.g.
    // instructions/error_localizer_enabled) are not dropped.
    if (
      fullEval &&
      (!bindingEdit || (hydrationReady && !initialBindingDraft)) &&
      (!initialLoadDone.current || !isDirty) &&
      !selectedVersionId
    ) {
      // Merge template config with any saved runtime overrides from the
      // user eval (run_config). Edit mode: evalData.run_config holds the
      // previously saved model, agentMode, passThreshold, etc.
      const rawRunConfig = bindingRunConfigOf(evalData);
      const bindingConfig =
        evalData?.bindingConfig || evalData?.binding_config || {};

      const normalizedRunConfig = {
        ...rawRunConfig,
        agent_mode:
          rawRunConfig.agent_mode ??
          rawRunConfig.agentMode ??
          evalData?.agent_mode ??
          evalData?.agentMode,
        check_internet:
          rawRunConfig.check_internet ??
          rawRunConfig.checkInternet ??
          evalData?.check_internet ??
          evalData?.checkInternet,
        knowledge_bases:
          rawRunConfig.knowledge_bases ??
          rawRunConfig.knowledgeBases ??
          evalData?.knowledge_bases ??
          evalData?.knowledgeBases,
        data_injection:
          rawRunConfig.data_injection ??
          rawRunConfig.dataInjection ??
          evalData?.data_injection ??
          evalData?.dataInjection,
        template_format:
          rawRunConfig.template_format ?? rawRunConfig.templateFormat,
        few_shot_examples:
          rawRunConfig.few_shot_examples ?? rawRunConfig.fewShotExamples,
        summary: rawRunConfig.summary ?? evalData?.summary,
        tools: rawRunConfig.tools ?? evalData?.tools,
        pass_threshold:
          rawRunConfig.pass_threshold ??
          rawRunConfig.passThreshold ??
          evalData?.pass_threshold ??
          evalData?.passThreshold,
        choice_scores:
          rawRunConfig.choice_scores ??
          rawRunConfig.choiceScores ??
          evalData?.choice_scores ??
          evalData?.choiceScores,
        multi_choice:
          rawRunConfig.multi_choice ??
          rawRunConfig.multiChoice ??
          evalData?.multi_choice ??
          evalData?.multiChoice,
        params: rawRunConfig.params ?? evalData?.params,
        // Multi-turn LLM evals store the full message chain on the template
        // config (fullEval.config.messages). Fall through every source so a
        // dataset / simulate / task / experiment scoped copy of a template
        // doesn't collapse into just the System turn on hydration.
        messages:
          rawRunConfig.messages ??
          evalData?.messages ??
          evalData?.config?.messages ??
          fullEval?.config?.messages,
      };
      Object.keys(normalizedRunConfig).forEach((key) => {
        if (normalizedRunConfig[key] === undefined)
          delete normalizedRunConfig[key];
      });
      // Match Save version's base when detail has no config, so hydration
      // does not overwrite the card's saved settings with editor defaults.
      const config = {
        ...(fullEval.config || evalData?.config || {}),
        ...bindingConfig,
        ...normalizedRunConfig,
      };
      const promptText = getEvalPromptText(fullEval, config);
      // Set template format BEFORE instructions so the InstructionEditor
      // mounts with the correct key and doesn't lose content on remount.
      setTemplateFormat(
        fullEval.template_format || config.template_format || "mustache",
      );
      // Type-aware split. Backend returns `instructions = template.criteria`
      // for every eval type — for code evals that field is the
      // description/criteria text, not a Jinja prompt. Only populate the
      // state slot that matches the eval type so the variable extractor
      // and editors see clean data.
      const _type =
        normalizedFullEval?.evalType || normalizedEvalData?.evalType || "llm";
      // Edit mode: an LLM eval's prompt is its saved system message (the
      // editor keeps instructions = the system message as it is edited), so
      // a reopened binding keeps the prompt it was saved with instead of
      // the template's (a decision judge asks that prompt, W1 review M2).
      const savedSystemPrompt =
        isEditMode && _type === "llm"
          ? (config.messages || []).find((m) => m?.role === "system")
              ?.content || ""
          : "";
      if (_type === "code") {
        setInstructions("");
        setCode(getEvalCode(normalizedFullEval));
      } else {
        setInstructions(savedSystemPrompt || promptText);
        setCode("");
      }
      setCodeLanguage(getEvalCodeLanguage(normalizedFullEval));
      // Priority: user's saved run-config override → canonical detail
      // (`fullEval.model`, full form e.g. "turing_small") → list-level
      // `evalData.model`. The list endpoint returns a stripped form
      // ("small") for built-in templates while detail returns the full
      // canonical value, so we intentionally prefer `fullEval.model`
      // over `evalData.model` to avoid the chip rendering "small".
      // Edit mode: the binding's own saved model comes first. A binding's
      // run config carries the template's config (and its config.model),
      // so reading config.model first swapped a saved judge back to the
      // template's model on reopen (W1 review M1).
      // Do NOT map `model` into `normalizedRunConfig` to "restore" the
      // saved value: `config` is spread run-config-last, so an explicit
      // `evalData.model` there lands in `config.model` and wins over
      // `fullEval.model`, reintroducing the stripped-form bug. The saved
      // model already arrives via `...rawRunConfig` once the backend
      // stops filtering it out of `run_config` (see `build_run_config_view`).
      setModel(
        (isEditMode ? savedBindingModelOf(evalData) : null) ||
          config?.model ||
          fullEval?.model ||
          evalData?.model ||
          "turing_large",
      );
      // W1: the saved decision question comes back with its model.
      setDecision(savedDecisionOf(config));
      setOutputType(fullEval.output_type || "pass_fail");
      // Prefer user's saved run_config overrides (edit flow) over template defaults
      setPassThreshold(config.pass_threshold ?? fullEval.pass_threshold ?? 0.5);
      // W1-4: the card's saved N/A switch (run_config), else the binding's,
      // else the template's (all merged into `config` above).
      setAllowNa(Boolean(config.allow_na));
      setChoiceScores(config.choice_scores || fullEval.choice_scores || {});
      setMultiChoice(config.multi_choice ?? fullEval.multi_choice ?? false);
      // Messages: use config.messages if present, otherwise build from
      // instructions — but only for llm/agent evals, since code evals
      // don't have a prompt to seed into a system message.
      if (config.messages && config.messages.length > 0) {
        setMessages(config.messages);
      } else if (_type === "llm" && promptText) {
        setMessages([{ role: "system", content: promptText }]);
      }
      if (config.few_shot_examples) {
        setFewShotExamples(config.few_shot_examples || []);
      }

      // Code-eval static params (e.g. `min_words`, `max_words` for
      // word_count_in_range). Prefer the saved instance value in edit
      // mode, then fall back to the template's run_config defaults.
      // This is what makes the Parameters section show the previously-
      // entered values when the user reopens an eval for editing.
      const savedParams =
        (evalData && (evalData.params || evalData.config?.params)) ||
        config.params ||
        config.run_config?.params ||
        null;
      if (savedParams && typeof savedParams === "object") {
        setCodeParams(savedParams);
      }

      // Runtime-override defaults from the template. These are the seed
      // values users start with; any + button click mutates local state and
      // gets saved as run_config when they click "Add Evaluation".
      if (config.agent_mode) {
        setAgentMode(config.agent_mode);
      }
      if (config.check_internet !== undefined) {
        setUseInternet(!!config.check_internet);
      }
      const summaryVal = config.summary?.type || config.summary || "concise";
      if (summaryVal) setSummaryType(summaryVal);
      if (Array.isArray(config.tools)) {
        setConnectorIds(config.tools);
      } else if (config.tools && typeof config.tools === "object") {
        setConnectorIds(
          Object.entries(config.tools)
            .filter(([, enabled]) => !!enabled)
            .map(([name]) => name),
        );
      }
      if (Array.isArray(config.knowledge_bases)) {
        setKnowledgeBaseIds(config.knowledge_bases);
      }
      const di = config.data_injection || config.run_config?.data_injection;
      if (di && typeof di === "object") {
        const opts = [];
        if (di.full_row || di.fullRow) opts.push("dataset_row");
        if (di.span_context || di.spanContext) opts.push("span_context");
        if (di.trace_context || di.traceContext) opts.push("trace_context");
        if (di.session_context || di.sessionContext)
          opts.push("session_context");
        if (di.call_context || di.callContext) opts.push("call_context");
        if (opts.length > 0) {
          setContextOptions(opts);
        } else if (di.variables_only || di.variablesOnly) {
          setContextOptions(["variables_only"]);
        }
      } else if (source === "task") {
        const seeded = contextOptionsForRowType(sourceRowType);
        if (seeded) setContextOptions(seeded);
      }
      const localizationDefault =
        config.error_localizer_enabled ??
        fullEval.error_localizer_enabled ??
        false;
      setErrorLocalizerEnabled(
        isEditMode
          ? bindingErrorLocalizerOf(evalData, localizationDefault)
          : localizationDefault,
      );

      // Edit mode: keep the saved name. Create mode: generate a unique name
      // from the template base name + source slug + date/time, e.g.
      // "is_good_summary_dataset_14_may_2026_15_42". The source slug ties
      // the name to where the eval was created (dataset / experiment /
      // workbench / …); the timestamp avoids collisions on the backend
      // uniqueness check for same-source same-day repeats.
      if (isEditMode || source === "composite") {
        setEvalName(evalData?.name || fullEval.name || "");
      } else {
        const baseName =
          fullEval.name ||
          normalizedEvalData?.name ||
          getEvalBaseName(fullEval);
        const sanitized = baseName
          .toLowerCase()
          .replace(/\s+/g, "_")
          .replace(/[^a-z0-9_-]/g, "");
        const sourceSlug = SOURCE_NAME_SLUGS[source] || "";
        const stamp = format(new Date(), "dd_MMM_yyyy_HH_mm").toLowerCase();
        const suffix = [sourceSlug, stamp].filter(Boolean).join("_");
        const maxBaseLen = Math.max(0, 50 - suffix.length - (suffix ? 1 : 0));
        const truncatedBase = sanitized.slice(0, maxBaseLen).replace(/_+$/, "");
        setEvalName(
          [truncatedBase, sourceSlug, stamp].filter(Boolean).join("_"),
        );
      }

      initialLoadDone.current = true;
      setDataReady(true);
      setHydratedVersionId(null);
      setHydrationInputs({ detail: fullEval, access: hydrationAccess });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    bindingEdit,
    hydrationReady,
    hydrationAccess,
    initialBindingDraft,
    fullEval,
    normalizedFullEval,
    normalizedEvalData,
    isEditMode,
    selectedVersionId,
  ]);

  // W1: picking a decision model starts its question from the eval (its
  // output type and choice scores, or the saved question); another
  // decision model keeps the question and takes its format. The question's
  // instructions are the eval's prompt itself (mapping.py build_question),
  // so the question carries none of its own. Score levels start empty: the
  // author writes them (JEV_SCORE_LEVELS_REQUIRED).
  const decisionModel = decisionModelOf(model);
  const decisionModelId = decisionModel?.id ?? null;
  const decisionMissing = decision == null;
  useEffect(() => {
    if (!decisionModel) return;
    setDecision((cur) => {
      const saved =
        savedDecisionOf(evalData?.config) ??
        savedDecisionOf(evalData?.bindingConfig) ??
        savedDecisionOf(evalData?.runConfig) ??
        savedDecisionOf(fullEval?.config) ??
        null;
      const start =
        cur ??
        saved ??
        defaultDecisionConfig({
          model,
          outputType,
          choiceScores,
        });
      const kind = outputKindOf(outputType);
      let next =
        start.model === decisionModel.id && start.output_kind === kind
          ? start
          : withModel({ ...start, output_kind: kind }, model);
      if (
        kind === "choices" &&
        !(next.choice?.labels ?? []).length &&
        Object.keys(choiceScores || {}).length
      )
        next = {
          ...next,
          choice: defaultDecisionConfig({ model, outputType, choiceScores })
            .choice,
        };
      if (kind === "score" && !Array.isArray(next.score?.levels))
        next = { ...next, score: { levels: [] } };
      const type = questionTypeOf(outputType, decisionModel.format).type;
      return next.question_type === (type ?? null)
        ? next
        : { ...next, question_type: type ?? null };
    });
    // Starts or re-points the question when the model or output type moves
    // (or a version without a question was picked).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [decisionModelId, outputType, decisionMissing]);
  // The mapped variables' data types (a decision reads text and JSON only).
  const inputDataTypes = useMemo(() => {
    const out = {};
    Object.entries(sourceMapping || {}).forEach(([variable, ref]) => {
      if (!ref) return;
      const id = String(ref).split(/[.[]/)[0];
      const col = (datasetColumns || []).find(
        (c) => c?.id === id || c?.name === id,
      );
      if (col?.data_type) out[variable] = col.data_type;
    });
    return out;
  }, [sourceMapping, datasetColumns]);
  const decisionErrors = useMemo(
    () =>
      decisionModelId
        ? decisionConfigErrors(decision, {
            multiChoice,
            instructions: evalType === "code" ? "" : instructions,
            evalType,
            fewShotExamples: evalType === "llm" ? fewShotExamples : [],
            agentMode,
            tools: connectorIds,
            knowledgeBases: knowledgeBaseIds,
            internet: useInternet,
            contextOptions,
            inputDataTypes,
          })
        : [],
    [
      decisionModelId,
      decision,
      multiChoice,
      instructions,
      evalType,
      fewShotExamples,
      agentMode,
      connectorIds,
      knowledgeBaseIds,
      useInternet,
      contextOptions,
      inputDataTypes,
    ],
  );
  // What the editor's Test sends for a decision judge: the question and the
  // eval's prompt as edited (its instructions), with the bar.
  // L7c g7 (editor:pass-threshold, editor:allow-na, editor:prompt,
  // test:editor): every other judge's Test sends the definition on screen
  // too (the bar, the prompt as edited, the N/A switch), so Test scores
  // what Update / Add will run, before anything is saved.
  const naShown =
    !isComposite &&
    allowNaApplies({
      evalType,
      isSystemEval,
      decisionModel: decisionModelId,
      supportsNa: fullEval?.supports_na,
    });
  const decisionTest = useMemo(() => {
    if (decisionModelId && decision)
      return { decision, rule_prompt: instructions, threshold: passThreshold };
    const def = {
      ...(outputType !== "pass_fail" && passThreshold != null
        ? { threshold: passThreshold }
        : {}),
      ...(evalType !== "code" && !isSystemEval && instructions?.trim()
        ? { rule_prompt: instructions }
        : {}),
      ...(naShown ? { allow_na: Boolean(allowNa) } : {}),
    };
    return Object.keys(def).length ? def : null;
  }, [
    decisionModelId,
    decision,
    instructions,
    passThreshold,
    outputType,
    evalType,
    isSystemEval,
    naShown,
    allowNa,
  ]);

  const handleTestResult = useCallback((success, result) => {
    setTestPassed(true);
    setTestError(
      success
        ? null
        : typeof result === "string"
          ? result
          : JSON.stringify(result),
    );
    setIsTesting(false);
  }, []);

  const handleClearTestResult = useCallback(() => {
    setTestPassed(false);
    setTestError(null);
  }, []);

  const handleTestEvaluation = useCallback(() => {
    // W1: a decision question the call cannot ask is not tested.
    if (decisionModelId && decisionErrors.length) {
      enqueueSnackbar(decisionErrors[0], { variant: "error" });
      return;
    }
    if (fagiLocked && evalType !== "code" && !model && !isComposite) {
      enqueueSnackbar("Please select a model.", { variant: "error" });
      setOpenModelMenuSignal((n) => n + 1);
      return;
    }
    setIsTesting(true);
    setTestError(null);
    setTestPassed(false);
    sourceRef.current?.runTest?.(templateId);
    // Safety timeout
    setTimeout(() => setIsTesting((v) => (v ? false : v)), 60000);
  }, [
    templateId,
    fagiLocked,
    evalType,
    model,
    isComposite,
    decisionModelId,
    decisionErrors,
  ]);

  // Build the prospective save once, before both the button and its handler.
  const draftSelection = useMemo(() => {
    const dataInjection = buildDataInjection(contextOptions);
    const tools = build_tools_payload(connectorIds);

    const templateType =
      fullEval?.template_type ||
      fullEval?.templateType ||
      evalData?.template_type;

    const resolvedConfig = buildEvalTemplateConfig({
      baseConfig: {
        ...selectedBaseConfig,
        ...(isEditMode ? evalData?.bindingConfig || {} : {}),
      },
      evalType,
      instructions,
      code,
      codeLanguage,
      messages,
      fewShotExamples,
      outputType,
      passThreshold,
      choiceScores,
      multiChoice,
      templateFormat,
    });

    if (!selectedVersionId) delete resolvedConfig.version_pin;

    // When the picker was opened in edit mode (initialEval came from a
    // saved-evals row or the column-menu Edit Eval action), evalData
    // carries `userEvalId` — the existing UserEvalMetric id. The host
    // (EvaluationDrawer) reads this to route to /edit_and_run_user_eval
    // instead of /add_user_eval, preventing duplicate bindings.
    const userEvalId = evalData?.userEvalId;

    // In edit mode: evalData.name is the saved instance name — skip
    // fullEval.name (template name) so it never overwrites the instance name.
    const resolvedName =
      source === "composite"
        ? fullEval?.name || evalData?.name || evalName
        : isEditMode
          ? evalName || evalData?.name
          : evalName || fullEval?.name || evalData?.name;

    if (templateType === "composite") {
      // Composite metrics don't carry prompt/model/output-type/choice-score
      // state — those live on each child template. Emit only the fields
      // the host needs to create a UserEvalMetric plus the per-binding
      // weight overrides.
      return {
        templateId,
        evalTemplateId: templateId,
        userEvalId,
        name: resolvedName,
        mapping: sourceMapping,
        evalTemplate: fullEval || evalData,
        evalType,
        templateType,
        config: stripLaunchAllowNa(fullEval?.config || evalData?.config),
        // shared-edit(F68): pin the version shown by Latest, not a moving alias.
        versionId:
          selectedVersionId === "latest"
            ? versions[0]?.id ?? null
            : selectedVersionId,
        data_injection: dataInjection,
        error_localizer_enabled: errorLocalizerActive,
        composite_weight_overrides: compositeChildWeights,
      };
    }

    return {
      templateId,
      evalTemplateId: templateId,
      userEvalId,
      name: resolvedName,
      // integration S10c: a typed eval names the judge its own questions ask
      // (none for a tree of checks), never the picker's default model.
      model: isTypedEvalType(evalType) ? typedOwnJudgeOf(fullEval) : model,
      mapping: sourceMapping,
      evalTemplate: fullEval || evalData,
      evalType,
      templateType,
      outputType,
      ...versionPinOf(selectedVersionId, versions),
      config: {
        ...(decisionModelId && decision
          ? { ...resolvedConfig, decision }
          : resolvedConfig),
        ...(versionPinOf(selectedVersionId, versions).versionPin
          ? {
              version_pin: versionPinOf(selectedVersionId, versions).versionPin,
            }
          : {}),
        ...(allowNaApplies({
          evalType,
          isSystemEval,
          decisionModel: decisionModelId,
          supportsNa: fullEval?.supports_na,
        })
          ? { allow_na: allowNa }
          : {}),
      },
      versionId: selectedVersionId,
      instructions,
      messages,
      pass_threshold: passThreshold,
      choice_scores: choiceScores,
      multi_choice: multiChoice,
      // Code-eval static params (function_params_schema inputs, e.g.
      // min_words / max_words). Persisted so the backend writes them onto
      // UserEvalMetric.config.params and future runs can read them via
      // kwargs. Empty object is fine — backend treats it the same as
      // "no static params" (eval body then reads kwargs.get(...) → None).
      params: evalType === "code" ? codeParams : {},
      // Runtime overrides — these become config.run_config on the backend.
      agent_mode: agentMode,
      check_internet: useInternet,
      summary:
        summaryType === "custom"
          ? { type: "custom", custom: "" }
          : { type: summaryType },
      tools,
      knowledge_bases: knowledgeBaseIds,
      data_injection: dataInjection,
      error_localizer_enabled: errorLocalizerActive,
    };
  }, [
    templateId,
    fullEval,
    selectedBaseConfig,
    evalData,
    evalName,
    isEditMode,
    model,
    sourceMapping,
    evalType,
    outputType,
    selectedVersionId,
    instructions,
    code,
    codeLanguage,
    messages,
    fewShotExamples,
    passThreshold,
    choiceScores,
    multiChoice,
    allowNa,
    isSystemEval,
    versions,
    codeParams,
    agentMode,
    useInternet,
    summaryType,
    connectorIds,
    knowledgeBaseIds,
    contextOptions,
    isComposite,
    compositeChildWeights,
    errorLocalizerActive,
    templateFormat,
    source,
    decisionModelId,
    decision,
  ]);

  const draftBinding = useMemo(
    () => buildBindingSavePayload(draftSelection),
    [draftSelection],
  );
  useEffect(() => {
    if (
      bindingEdit &&
      hydrationReady &&
      versionReady &&
      hydrationInputs?.detail === fullEval &&
      hydrationInputs?.access === hydrationAccess &&
      dataReady &&
      hydratedVersionId === selectedVersionId &&
      (!isComposite || compositeReady) &&
      !initialBindingDraft
    ) {
      // No editor or mapping panel is mounted until this baseline exists.
      // Capture only a hydration pass using the loaded detail and settled gates;
      // Save version must never reset the still-unsaved binding's baseline.
      // Let derived control effects (e.g. the decision question) settle too.
      if (baselineCandidate !== draftBinding)
        setBaselineCandidate(draftBinding);
      else {
        setInitialBindingDraft(draftBinding);
        setStoredVersionConfig(selectedBaseConfig);
      }
    }
  }, [
    bindingEdit,
    hydrationReady,
    versionReady,
    hydrationInputs,
    fullEval,
    hydrationAccess,
    dataReady,
    hydratedVersionId,
    selectedVersionId,
    isComposite,
    compositeReady,
    initialBindingDraft,
    baselineCandidate,
    draftBinding,
    selectedBaseConfig,
  ]);
  const storedBinding = useMemo(
    () =>
      savedBindingPayload ||
      (initialBindingDraft &&
        storedBindingPayload(evalData, initialBindingDraft)),
    [savedBindingPayload, initialBindingDraft, evalData],
  );
  const bindingUpdate = useMemo(() => {
    if (!bindingEdit || !initialBindingDraft)
      return { payload: draftBinding, changed: !bindingEdit };
    return reconcileBindingSave(
      storedBinding,
      initialBindingDraft,
      draftBinding,
      isSystemEval,
      selectedBaseConfig,
      savedVersionBaseline,
      storedVersionConfig,
    );
  }, [
    bindingEdit,
    initialBindingDraft,
    draftBinding,
    storedBinding,
    isSystemEval,
    selectedBaseConfig,
    savedVersionBaseline,
    storedVersionConfig,
  ]);
  const canUpdate = !bindingEdit || bindingUpdate.changed;
  const hasPendingChanges = bindingEdit ? bindingUpdate.changed : isDirty;
  const versionConfig = useMemo(
    () =>
      bindingEdit && initialBindingDraft
        ? stripLaunchAllowNa(
            buildBindingVersionConfig(
              savedVersionBaseline
                ? { config: { config: savedVersionBaseline.config } }
                : storedBinding,
              savedVersionBaseline?.draft ?? initialBindingDraft,
              draftBinding,
              savedVersionBaseline?.config ?? selectedBaseConfig,
            ),
          )
        : {
            ...stripLaunchAllowNa(selectedBaseConfig),
            ...draftBinding.config.config,
            ...draftBinding.config.run_config,
          },
    [
      bindingEdit,
      initialBindingDraft,
      storedBinding,
      savedVersionBaseline,
      draftBinding,
      selectedBaseConfig,
    ],
  );

  const handleAdd = useCallback(async () => {
    if (!canUpdate) return;
    if (decisionModelId && decisionErrors.length) {
      enqueueSnackbar(decisionErrors[0], { variant: "error" });
      return;
    }
    if (fagiLocked && evalType !== "code" && !model && !isComposite) {
      enqueueSnackbar("Please select a model.", { variant: "error" });
      setOpenModelMenuSignal((n) => n + 1);
      return;
    }
    // When opened from an optimization context, enforce that the optimized
    // column is mapped to at least one eval input field. Without this the
    // backend's get_metrics_by_column filter would exclude the eval from the
    // optimization's eval list.
    if (requiredColumnId) {
      const mappingValues = Object.values(sourceMapping || {});
      if (!mappingValues.includes(requiredColumnId)) {
        const requiredCol = sourceColumns?.find(
          (col) => (col?.field || col?.id || col?.col?.id) === requiredColumnId,
        );
        const colLabel =
          requiredCol?.headerName ||
          requiredCol?.col?.name ||
          requiredCol?.label ||
          "the optimization column";
        enqueueSnackbar(
          `At least one field must be mapped to "${colLabel}" to use this evaluation in the optimization`,
          { variant: "error" },
        );
        return;
      }
      if (evalType === "llm" && !hasValidPromptMessages) {
        const errorMessage = "Prompt message is required";
        setPromptMessageError(errorMessage);
        enqueueSnackbar(errorMessage, { variant: "error" });
        return;
      }
    }

    if (source === "task" && onFiltersChange) {
      onFiltersChange(localFilterForm.getValues("filters") || []);
    }

    try {
      const saved = await onSave(
        bindingEdit
          ? {
              ...draftSelection,
              versionId: bindingUpdate.payload.pinned_version_id,
              bindingPayload: bindingUpdate.payload,
            }
          : draftSelection,
      );
      if (bindingEdit && saved !== false) {
        // A host may keep the drawer open. Rebase to what was submitted, not
        // any newer in-flight edits; rejected saves keep the previous baseline.
        setSavedBindingPayload(bindingUpdate.payload);
        setInitialBindingDraft(draftBinding);
        setStoredVersionConfig(
          bindingUpdate.payload.pinned_version_id ? selectedBaseConfig : null,
        );
        setSavedVersionBaseline({ draft: draftBinding, config: versionConfig });
      }
    } catch {
      // The host owns error reporting; leave edits available for retry.
    }
  }, [
    draftBinding,
    versionConfig,
    selectedBaseConfig,
    canUpdate,
    bindingEdit,
    bindingUpdate,
    draftSelection,
    onSave,
    decisionModelId,
    decisionErrors,
    fagiLocked,
    evalType,
    model,
    isComposite,
    requiredColumnId,
    sourceMapping,
    sourceColumns,
    hasValidPromptMessages,
    source,
    onFiltersChange,
    localFilterForm,
  ]);

  // ── Save as new version (user evals only) ──
  // Mirrors EvalDetailPage.handleSaveVersion: updates the template fields,
  // then creates a new EvalTemplateVersion with a config snapshot. After
  // save, the new version becomes the selected version in the tabs so
  // the user can Apply it to the dataset with the existing
  // "Add Evaluation" button.
  const handleSaveVersion = useCallback(async () => {
    if (isSystemEval || (bindingEdit && !initialBindingDraft)) return;
    if (agentEvalLocked && evalType === "agent") {
      enqueueSnackbar(
        "Agent evaluations aren't enabled for this workspace. Use LLM-as-a-Judge or Code evaluations instead.",
        { variant: "error" },
      );
      return;
    }
    if (fagiLocked && FAGI_MODEL_VALUES.has(model)) {
      enqueueSnackbar(
        "Turing models aren't enabled for this workspace. Please select your own model.",
        { variant: "error" },
      );
      setOpenModelMenuSignal((n) => n + 1);
      return;
    }
    if (fagiLocked && evalType !== "code" && !model) {
      enqueueSnackbar("Please select a model.", { variant: "error" });
      setOpenModelMenuSignal((n) => n + 1);
      return;
    }
    // W1: a decision judge's question is saved with the template as
    // config.jev_mapping (jev_templates.py validate_jev_template_save), so a
    // question the call cannot ask is not saved.
    if (decisionModelId && decisionErrors.length) {
      enqueueSnackbar(decisionErrors[0], { variant: "error" });
      return;
    }
    const jevPayload =
      decisionModelId && decision ? { jev_mapping: decision } : {};
    try {
      const payload = {
        instructions:
          evalType === "code" ? undefined : instructions || undefined,
        code: evalType === "code" ? code : undefined,
        code_language: evalType === "code" ? codeLanguage : undefined,
        model,
        output_type: outputType,
        pass_threshold: passThreshold,
        choice_scores:
          Object.keys(choiceScores || {}).length > 0 ? choiceScores : null,
        multi_choice: multiChoice,
        messages: evalType === "llm" ? messages : undefined,
        few_shot_examples: evalType === "llm" ? fewShotExamples : undefined,
        ...jevPayload,
      };
      await updateEval.mutateAsync(payload);

      const configSnapshot = {
        ...versionConfig,
        rule_prompt: evalType === "code" ? "" : instructions,
        code: evalType === "code" ? code : undefined,
        language: evalType === "code" ? codeLanguage : undefined,
        model,
        output:
          {
            pass_fail: "Pass/Fail",
            percentage: "score",
            deterministic: "choices",
          }[outputType] || "Pass/Fail",
        pass_threshold: passThreshold,
        choice_scores:
          Object.keys(choiceScores || {}).length > 0 ? choiceScores : undefined,
        multi_choice: multiChoice,
        messages: evalType === "llm" ? messages : undefined,
        few_shot_examples: evalType === "llm" ? fewShotExamples : undefined,
        ...jevPayload,
      };
      if (!jevPayload.jev_mapping) delete configSnapshot.jev_mapping;
      const newVersion = await createVersion.mutateAsync({
        config_snapshot: configSnapshot,
        criteria: evalType === "code" ? code : instructions,
        model,
      });
      enqueueSnackbar(
        `Version V${newVersion?.version_number || newVersion?.versionNumber || ""} saved`,
        { variant: "success" },
      );
      setIsDirty(false);
      // Jump to the new version so the Apply button picks it up
      if (newVersion?.id) {
        // The async versions refresh must not hydrate old binding overrides
        // over the settings just saved in this version.
        locallySavedVersion.current = {
          ...newVersion,
          config_snapshot: configSnapshot,
        };
        setSavedVersionBaseline({
          draft: { ...draftBinding, pinned_version_id: newVersion.id },
          config: configSnapshot,
        });
        setSelectedVersionId(newVersion.id);
      }
    } catch (err) {
      enqueueSnackbar(
        getSafeActionErrorMessage(err, "Failed to save version"),
        { variant: "error" },
      );
    }
  }, [
    bindingEdit,
    initialBindingDraft,
    draftBinding,
    versionConfig,
    isSystemEval,
    agentEvalLocked,
    fagiLocked,
    evalType,
    instructions,
    code,
    codeLanguage,
    model,
    outputType,
    passThreshold,
    choiceScores,
    multiChoice,
    messages,
    fewShotExamples,
    updateEval,
    createVersion,
    decisionModelId,
    decision,
    decisionErrors,
  ]);

  if (isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100%",
          py: 8,
        }}
      >
        <CircularProgress size={32} />
      </Box>
    );
  }

  if (!templateId || (isError && !fullEval)) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          gap: 2,
          py: 8,
        }}
      >
        <Iconify
          icon="mdi:alert-circle-outline"
          width={40}
          sx={{ color: "text.disabled" }}
        />
        <Typography role="alert" variant="body2" color="text.secondary">
          {!templateId
            ? "No template ID found for this evaluation."
            : "Failed to load evaluation details."}
        </Typography>
        {templateId && (
          <Button
            size="small"
            variant="outlined"
            onClick={() => refetchTemplate()}
          >
            Retry
          </Button>
        )}
        <Button
          size="small"
          variant="outlined"
          onClick={onBack}
          startIcon={<Iconify icon="solar:arrow-left-linear" width={16} />}
          sx={{ textTransform: "none" }}
        >
          Back to list
        </Button>
      </Box>
    );
  }

  // Keep all mutable controls unmounted until their comparison baseline is
  // established. A delayed version response must never absorb a user's edit.
  if (bindingEdit && !initialBindingDraft) {
    const versionFailed = Boolean(versionsError && !versionReady);
    const versionUnavailable = Boolean(
      storedPinReference && !versionsLoading && versionsData && !versionReady,
    );
    const compositeFailed = isComposite && compositeError && !compositeDetail;
    const recoveryMessage = compositeFailed
      ? "Failed to load evaluation details."
      : versionFailed
        ? "Failed to load evaluation versions."
        : versionUnavailable
          ? "Saved evaluation version is unavailable."
          : null;
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          gap: 2,
          py: 8,
        }}
      >
        {recoveryMessage ? (
          <>
            <Typography role="alert" variant="body2" color="text.secondary">
              {recoveryMessage}
            </Typography>
            <Button
              size="small"
              variant="outlined"
              onClick={() =>
                compositeFailed ? refetchComposite() : refetchVersions()
              }
            >
              Retry
            </Button>
          </>
        ) : (
          <CircularProgress size={32} aria-label="Loading" />
        )}
        <Button size="small" variant="outlined" onClick={onBack}>
          Back to list
        </Button>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* ── Header ── */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          pb: 1.5,
          flexShrink: 0,
        }}
      >
        <Box
          sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0 }}
        >
          <IconButton size="small" onClick={onBack} sx={{ p: 0.5 }}>
            <Iconify icon="solar:arrow-left-linear" width={18} />
          </IconButton>
          <Typography variant="subtitle1" fontWeight={600} noWrap>
            {fullEval?.name || evalData?.name}
          </Typography>
          <ShowComponent condition={!isSystemEval}>
            {/* L7c g7 (editor:header): the badge names the version being
                configured (the select's), else the template's current. */}
            <span title={RECORDED_VERSION_TOOLTIP}>
              <VersionBadge version={`Version label: ${shownVersion}`} />
            </span>
          </ShowComponent>
          <EvalTypeBadge type={evalType} />
          {hasPendingChanges && (
            <Chip
              label="Edited"
              size="small"
              color="warning"
              variant="outlined"
              sx={{ fontSize: "10px", height: 18 }}
            />
          )}
        </Box>
        {/* Save version is legacy for every source; execution-pin isolation
            belongs at the creation payload boundary, not around this action. */}
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          {/* Host-supplied progress (multi-eval queue walk). Built-in evals
              hide the version controls, so a bar floating mid-header reads as
              misplaced — it sits with the version controls instead. */}
          {progress}
          {/* Save-as-new-version — user evals only. System evals are
              read-only by product policy (editing triggers Copy flow).
              Sits beside the existing version dropdown so the user can
              edit, save a new version, then pick it from the dropdown
              and click Add Evaluation. */}
          {/* integration S10b: a typed eval saves its versions in its own editor
              (Edit definition); H-A2 lets a run pin one. */}
          {!isSystemEval && !isComposite && !isTypedEvalType(evalType) && (
            <LoadingButton
              size="small"
              variant="outlined"
              color="primary"
              loading={updateEval.isPending || createVersion.isPending}
              disabled={updateEval.isPending || createVersion.isPending}
              onClick={handleSaveVersion}
              startIcon={<Iconify icon="solar:diskette-bold" width={14} />}
              sx={{
                textTransform: "none",
                fontSize: "12px",
                height: 30,
                px: 1.25,
              }}
            >
              Save version
            </LoadingButton>
          )}
          {showClose && (
            <IconButton onClick={onClose} size="small" sx={{ p: 0.5 }}>
              <Iconify icon="mingcute:close-line" width={20} />
            </IconButton>
          )}
        </Box>
      </Box>

      {/* ── Eval Name ── */}
      <Box sx={{ py: 1.5, flexShrink: 0 }}>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
          Name
          <RequiredMark />
        </Typography>
        <TextField
          fullWidth
          size="small"
          placeholder="e.g. toxicity-check, my-custom-eval"
          value={evalName}
          disabled={isEditMode} // Name is not editable in edit mode — it's the name of the UserEvalMetric instance, not the template
          onChange={(e) => {
            // Sanitise: lowercase, replace spaces with hyphens, only allow a-z 0-9 _ -
            const raw = e.target.value
              .toLowerCase()
              .replace(/\s+/g, "-")
              .replace(/[^a-z0-9_-]/g, "");
            setEvalName(raw);
            setIsDirty(true);
          }}
          error={!isEditMode && evalName.length >= 51}
          helperText={
            isEditMode
              ? undefined
              : evalName.length >= 51
                ? "Name can't be longer than 50 characters"
                : `Lowercase letters, numbers, hyphens and underscores only · ${evalName.length}/50`
          }
          FormHelperTextProps={{ sx: { fontSize: "11px", mt: 0.25, mx: 0 } }}
          sx={{ "& .MuiInputBase-root": { fontSize: "13px", height: 34 } }}
        />
      </Box>

      <Divider />

      {/* ── Two-panel layout (same as EvalDetailPage) ── */}
      <Box sx={{ flex: 1, minHeight: 0, pt: 1.5 }}>
        <ResizablePanels
          initialLeftWidth={55}
          minLeftWidth={35}
          maxLeftWidth={75}
          showIcon
          leftPanel={
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: 2.5,
                pr: 2,
                width: "100%",
                minWidth: 0,
                height: "100%",
                overflow: "auto",
              }}
            >
              {/* Composite body: compact summary with children + editable weights.
                  Mirrors the single-eval config flow style instead of the
                  full composite edit panel. */}
              {isComposite && (
                <Box
                  sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
                >
                  {/* Aggregation info */}
                  <Box
                    sx={{
                      p: 1.5,
                      borderRadius: 1,
                      border: "1px solid",
                      borderColor: "divider",
                      backgroundColor: (t) =>
                        t.palette.mode === "dark"
                          ? "rgba(255,255,255,0.02)"
                          : "rgba(0,0,0,0.01)",
                    }}
                  >
                    <Typography
                      variant="caption"
                      fontWeight={600}
                      sx={{ mb: 0.5, display: "block" }}
                    >
                      Aggregation
                    </Typography>
                    <Typography variant="body2" sx={{ fontSize: "12px" }}>
                      {(
                        compositeDetail?.aggregation_function ||
                        fullEval?.aggregation_function ||
                        "weighted_avg"
                      ).replace(/_/g, " ")}
                      {(compositeDetail?.aggregation_enabled ??
                        fullEval?.aggregation_enabled) === false &&
                        " (disabled)"}
                    </Typography>
                  </Box>

                  {/* Children with editable weights */}
                  <Box>
                    <Typography
                      variant="caption"
                      fontWeight={600}
                      sx={{ mb: 0.75, display: "block" }}
                    >
                      Child Evaluators (
                      {(compositeDetail?.children || []).length})
                    </Typography>
                    <Box
                      sx={{
                        display: "flex",
                        flexDirection: "column",
                        gap: 0.75,
                      }}
                    >
                      {(compositeDetail?.children || []).map((child, i) => (
                        <Box
                          key={child.child_id}
                          sx={{
                            p: 1,
                            borderRadius: 1,
                            border: "1px solid",
                            borderColor: "divider",
                          }}
                        >
                          <Box
                            sx={{
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "space-between",
                            }}
                          >
                            <Box sx={{ flex: 1, minWidth: 0 }}>
                              <Typography
                                variant="body2"
                                fontWeight={600}
                                noWrap
                                sx={{ fontSize: "12px" }}
                              >
                                #{i + 1} {child.child_name}
                              </Typography>
                              <Typography
                                variant="caption"
                                color="text.secondary"
                              >
                                {child.eval_type || "llm"}
                              </Typography>
                            </Box>
                            <Box
                              sx={{
                                display: "flex",
                                alignItems: "center",
                                gap: 0.5,
                                flexShrink: 0,
                              }}
                            >
                              <Typography
                                variant="caption"
                                color="text.secondary"
                                sx={{ fontSize: "11px" }}
                              >
                                Weight
                              </Typography>
                              <input
                                type="number"
                                min="0"
                                step="0.1"
                                value={
                                  compositeChildWeights[child.child_id] ??
                                  child.weight ??
                                  1
                                }
                                onChange={(e) => {
                                  const v = parseFloat(e.target.value) || 0;
                                  setCompositeChildWeights((prev) => ({
                                    ...prev,
                                    [child.child_id]: v,
                                  }));
                                  setIsDirty(true);
                                }}
                                style={{
                                  width: 50,
                                  padding: "2px 6px",
                                  fontSize: "12px",
                                  borderRadius: 4,
                                  border:
                                    "1px solid var(--mui-palette-divider, #444)",
                                  background: "transparent",
                                  color: "inherit",
                                  textAlign: "center",
                                }}
                              />
                            </Box>
                          </Box>
                          <CompositeChildParams params={child.config?.params} />
                        </Box>
                      ))}
                    </Box>
                  </Box>
                </Box>
              )}

              {/* System-eval notice: the prompt is shown read-only so users
                  can see what the built-in evaluation checks, but they don't
                  need to (and can't) edit it. Without this copy, users are
                  unsure whether they're supposed to write a prompt. */}
              {isSystemEval &&
                !isComposite &&
                (evalType === "agent" || evalType === "llm") &&
                dataReady && (
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 0.75,
                      color: "text.secondary",
                    }}
                  >
                    <Iconify
                      icon="eva:info-outline"
                      width={14}
                      sx={{ flexShrink: 0 }}
                    />
                    <Typography
                      variant="caption"
                      sx={{ fontSize: "11px", lineHeight: 1.4 }}
                    >
                      Built-in evaluation: prompt is pre-configured and shown
                      for reference only.
                    </Typography>
                  </Box>
                )}

              {/* integration S10: a typed eval is edited in Evals; its summary here */}
              {!isComposite && isTypedEvalType(evalType) && (
                <TypedEvalBindingConfig
                  evalType={evalType}
                  evalData={fullEval}
                  templateId={templateId}
                />
              )}

              {/* Agent type — only render after data loads so Quill
                  initializes with the actual instructions content */}
              {!isComposite && evalType === "agent" && dataReady && (
                <InstructionEditor
                  openModelMenuSignal={openModelMenuSignal}
                  key={`agent-${templateId}`}
                  value={instructions}
                  onChange={(v) => {
                    setInstructions(v);
                    setIsDirty(true);
                  }}
                  model={model}
                  onModelChange={(v) => {
                    setModel(v);
                    setIsDirty(true);
                  }}
                  templateFormat={templateFormat}
                  onTemplateFormatChange={setTemplateFormat}
                  datasetColumns={datasetColumns}
                  datasetJsonSchemas={datasetJsonSchemas}
                  mappedVariables={sourceMapping}
                  disabled={isInstructionsReadOnly}
                  modelSelectorDisabled={false}
                  mode={agentMode}
                  onModeChange={(v) => {
                    setAgentMode(v);
                    setIsDirty(true);
                  }}
                  useInternet={useInternet}
                  onUseInternetChange={(v) => {
                    setUseInternet(v);
                    setIsDirty(true);
                  }}
                  activeSummary={summaryType}
                  onActiveSummaryChange={(v) => {
                    setSummaryType(v);
                    setIsDirty(true);
                  }}
                  activeConnectorIds={connectorIds}
                  onActiveConnectorIdsChange={(v) => {
                    setConnectorIds(v);
                    setIsDirty(true);
                  }}
                  selectedKBs={knowledgeBaseIds}
                  onSelectedKBsChange={(v) => {
                    setKnowledgeBaseIds(v);
                    setIsDirty(true);
                  }}
                  activeContextOptions={contextOptions}
                  onActiveContextOptionsChange={(v) => {
                    setContextOptions(v);
                    setIsDirty(true);
                  }}
                  hideDatasetContextToggle={source === "task"}
                />
              )}

              {/* LLM type. ModelSelector is rendered inline in
                  LLMPromptEditor's top bar (alongside the template
                  format selector) so LLM-as-a-judge has the same
                  top-bar layout as the agent InstructionEditor. */}
              {!isComposite && evalType === "llm" && dataReady && (
                <>
                  <LLMPromptEditor
                    openModelMenuSignal={openModelMenuSignal}
                    key={`llm-${templateId}`}
                    messages={messages}
                    onMessagesChange={(msgs) => {
                      setMessages(msgs);
                      const sysMsg = msgs.find((m) => m.role === "system");
                      if (sysMsg) setInstructions(sysMsg.content);
                      if (hasNonEmptyPromptMessage(msgs)) {
                        setPromptMessageError("");
                      }
                      setIsDirty(true);
                    }}
                    templateFormat={templateFormat}
                    onTemplateFormatChange={setTemplateFormat}
                    model={model}
                    onModelChange={(v) => {
                      setModel(v);
                      setIsDirty(true);
                    }}
                    datasetColumns={datasetColumns}
                    datasetJsonSchemas={datasetJsonSchemas}
                    disabled={isInstructionsReadOnly}
                    modelSelectorDisabled={false}
                  />
                  <FewShotExamples
                    selectedDatasets={fewShotExamples}
                    onChange={(v) => {
                      setFewShotExamples(v);
                      setIsDirty(true);
                    }}
                    disabled={isInstructionsReadOnly}
                  />
                  {promptMessageError && (
                    <Typography variant="caption" color="error.main">
                      {promptMessageError}
                    </Typography>
                  )}
                </>
              )}

              {/* W1: a decision-model judge's question */}
              {!isComposite &&
                evalType !== "code" &&
                dataReady &&
                decisionModel &&
                decision && (
                  <DecisionQuestionEditor
                    value={decision}
                    outputType={outputType}
                    variables={variables}
                    instructions={instructions}
                    errors={decisionErrors}
                    onChange={(next) => {
                      setDecision(next);
                      setIsDirty(true);
                    }}
                  />
                )}

              {/* Code type */}
              {!isComposite && evalType === "code" && dataReady && (
                <CodeEvalEditor
                  key={`code-${templateId}`}
                  code={code}
                  setCode={(v) => {
                    setCode(v);
                    setIsDirty(true);
                  }}
                  codeLanguage={codeLanguage}
                  setCodeLanguage={(v) => {
                    setCodeLanguage(v);
                    setIsDirty(true);
                  }}
                  datasetColumns={datasetColumns}
                />
              )}

              {/* Output Type (not applicable to composites) */}
              {isComposite || isTypedEvalType(evalType) ? null : evalType ===
                "code" ? (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                    Scoring
                  </Typography>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ mb: 1.5, display: "block" }}
                  >
                    Code evaluator returns a score between 0 and 1. Set a pass
                    threshold below.
                  </Typography>
                  <Typography
                    variant="subtitle2"
                    sx={{ mb: 0.5, color: "text.primary" }}
                  >
                    Pass Threshold
                  </Typography>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ mb: 1, display: "block" }}
                  >
                    Scores at or above this threshold are considered a pass.
                  </Typography>
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 2,
                      px: 1,
                    }}
                  >
                    <Typography variant="caption">0</Typography>
                    <Slider
                      value={Math.round(passThreshold * 100)}
                      onChange={(_, val) => {
                        setPassThreshold(val / 100);
                        setIsDirty(true);
                      }}
                      min={0}
                      max={100}
                      size="small"
                      valueLabelDisplay="auto"
                      valueLabelFormat={(v) => `${Math.round(v)}%`}
                      disabled={false}
                    />
                    <Typography variant="caption">100%</Typography>
                  </Box>
                </Box>
              ) : (
                <OutputTypeConfig
                  outputType={outputType}
                  onOutputTypeChange={(v) => {
                    setOutputType(v);
                    setIsDirty(true);
                  }}
                  choiceScores={choiceScores}
                  onChoiceScoresChange={(v) => {
                    setChoiceScores(v);
                    setIsDirty(true);
                  }}
                  multiChoice={multiChoice}
                  onMultiChoiceChange={(v) => {
                    setMultiChoice(v);
                    setIsDirty(true);
                  }}
                  passThreshold={passThreshold}
                  onPassThresholdChange={(v) => {
                    setPassThreshold(v);
                    setIsDirty(true);
                  }}
                  disabled={isSystemEval}
                  categoryLocked={isOutputTypeCategoryLocked}
                />
              )}
              {!isComposite &&
                allowNaApplies({
                  evalType,
                  isSystemEval,
                  decisionModel: decisionModelId,
                  supportsNa: fullEval?.supports_na,
                }) && (
                  <AllowNaSwitch
                    checked={allowNa}
                    onChange={(v) => {
                      setAllowNa(v);
                      setIsDirty(true);
                    }}
                  />
                )}

              {/* Single-eval concern, LLM/Agent only — code evals don't produce
                  the model traces the feature introspects. */}
              {errorLocalizerAvailable &&
                !isComposite &&
                evalType !== "code" &&
                !isTypedEvalType(evalType) && (
                  <CustomTooltip
                    show={agentEvalLocked}
                    type=""
                    arrow
                    title={ERROR_LOCALIZER_LOCKED_TOOLTIP}
                  >
                    <FormControlLabel
                      control={
                        <Checkbox
                          checked={errorLocalizerActive}
                          disabled={agentEvalLocked}
                          onChange={(e) => {
                            setErrorLocalizerEnabled(e.target.checked);
                            setIsDirty(true);
                          }}
                          size="small"
                        />
                      }
                      label={
                        <Box>
                          <Typography variant="body2" fontWeight={500}>
                            Error Localization
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ display: "block" }}
                          >
                            Pinpoints which parts of the input caused evaluation
                            failures
                          </Typography>
                        </Box>
                      }
                      sx={{ alignItems: "flex-start" }}
                    />
                  </CustomTooltip>
                )}
            </Box>
          }
          rightPanel={
            <Box
              sx={{
                pl: 2,
                width: "100%",
                minWidth: 0,
                height: "100%",
                display: "flex",
                flexDirection: "column",
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 0.75,
                  mb: 1.5,
                }}
              >
                <Typography
                  variant="body2"
                  fontWeight={600}
                  sx={{ fontSize: "13px" }}
                >
                  {source === "composite"
                    ? "Test Playground"
                    : SOURCE_LABELS[source]
                      ? `${SOURCE_LABELS[source]} — Variable Mapping`
                      : "Variable mapping"}
                </Typography>
                {source === "task" && ROW_TYPE_LABELS[sourceRowType] && (
                  <Chip
                    label={ROW_TYPE_LABELS[sourceRowType]}
                    size="small"
                    sx={{
                      height: 18,
                      fontSize: "10px",
                      bgcolor: "background.neutral",
                      color: "text.secondary",
                      "& .MuiChip-label": { px: 0.75 },
                    }}
                  />
                )}
              </Box>

              {source === "task" && sourceId && (
                <Box sx={{ mb: 1.5 }}>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ fontSize: "12px", display: "block", mb: 0.75 }}
                  >
                    Narrow down which{" "}
                    {(ROW_TYPE_LABELS[sourceRowType] || "rows").toLowerCase()}{" "}
                    this task runs on
                  </Typography>
                  <TaskFilterBar
                    control={localFilterForm.control}
                    setValue={localFilterForm.setValue}
                    projectId={sourceId}
                    isSimulator={String(sourceRowType || "")
                      .toLowerCase()
                      .startsWith("voice")}
                    rowType={sourceRowType}
                  />
                </Box>
              )}

              <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", pb: 2 }}>
                {(source === "dataset" ||
                  source === "experiment" ||
                  source === "workbench" ||
                  source === "run-experiment" ||
                  source === "run-optimization") && (
                  <DatasetTestMode
                    ref={sourceRef}
                    decisionTest={decisionTest}
                    templateId={templateId}
                    // shared-edit(F68): include structured jury inputs in dataset mappings.
                    variables={datasetModeVariables}
                    model={model}
                    codeParams={codeParams}
                    onTestResult={handleTestResult}
                    onClearResult={handleClearTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    initialDatasetId={sourceId}
                    onReadyChange={handleSourceReadyChange}
                    contextOptions={contextOptions}
                    errorLocalizerEnabled={errorLocalizerActive}
                    initialMapping={evalData?.mapping}
                    {...compositeSourceModeProps}
                    sourceColumns={
                      source === "workbench" ? sourceColumns : null
                    }
                    extraColumns={extraColumns}
                  />
                )}
                {source === "tracing" && (
                  <TracingTestMode
                    ref={sourceRef}
                    decisionTest={decisionTest}
                    templateId={templateId}
                    model={model}
                    variables={sourceModeVariables}
                    codeParams={codeParams}
                    onTestResult={handleTestResult}
                    onClearResult={handleClearTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    onReadyChange={handleSourceReadyChange}
                    hasDataInjection={hasDataInjection}
                    errorLocalizerEnabled={errorLocalizerActive}
                    initialMapping={evalData?.mapping}
                    {...compositeSourceModeProps}
                  />
                )}
                {(source === "simulation" || source === "test") && (
                  <SimulationTestMode
                    ref={sourceRef}
                    decisionTest={decisionTest}
                    templateId={templateId}
                    model={model}
                    variables={sourceModeVariables}
                    codeParams={codeParams}
                    onTestResult={handleTestResult}
                    onClearResult={handleClearTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    onReadyChange={handleSourceReadyChange}
                    errorLocalizerEnabled={errorLocalizerActive}
                    initialMapping={evalData?.mapping}
                    initialRunTestId={sourceId}
                    {...compositeSourceModeProps}
                  />
                )}
                {source === "create-simulate" && (
                  <CreateSimulationPreviewMode
                    ref={sourceRef}
                    variables={variables}
                    onTestResult={handleTestResult}
                    onClearResult={handleClearTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    onReadyChange={handleSourceReadyChange}
                    previewData={sourcePreviewData}
                    initialMapping={evalData?.mapping}
                  />
                )}
                {source === "task" && (
                  <TracingTestMode
                    ref={sourceRef}
                    decisionTest={decisionTest}
                    templateId={templateId}
                    model={model}
                    variables={sourceModeVariables}
                    codeParams={codeParams}
                    onTestResult={handleTestResult}
                    onClearResult={handleClearTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    onReadyChange={handleSourceReadyChange}
                    hasDataInjection={hasDataInjection}
                    initialProjectId={sourceId}
                    initialRowType={sourceRowType}
                    initialMapping={evalData?.mapping}
                    errorLocalizerEnabled={errorLocalizerActive}
                    localFilters={localApiFilters}
                    allowCustomFieldPath
                    {...compositeSourceModeProps}
                  />
                )}
                {source === "custom" && (
                  <DatasetTestMode
                    ref={sourceRef}
                    decisionTest={decisionTest}
                    templateId={templateId}
                    variables={variables}
                    model={model}
                    codeParams={codeParams}
                    onTestResult={handleTestResult}
                    onClearResult={handleClearTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    initialDatasetId={sourceId}
                    onReadyChange={handleSourceReadyChange}
                    contextOptions={contextOptions}
                    errorLocalizerEnabled={errorLocalizerActive}
                    {...compositeSourceModeProps}
                  />
                )}
                {source === "composite" && (
                  // Use the same rich preview component as the eval
                  // details page so users can run the eval against any
                  // source (dataset / tracing / simulation) before
                  // committing it to the composite. The internal tabs
                  // let them switch sources and pick a dataset/trace
                  // even though the composite itself isn't bound yet.
                  <TestPlayground
                    ref={sourceRef}
                    templateId={templateId}
                    evalName={evalName || ""}
                    instructions={evalType === "code" ? "" : instructions}
                    evalType={evalType}
                    isSystemEval={isSystemEval}
                    requiredKeys={variables}
                    showVersions={!(isSystemEval && evalType === "code")}
                    onTestResult={handleTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    isComposite={false}
                    model={model}
                    functionParamsSchema={functionParamsSchema}
                    configParamsDesc={configParamsDesc}
                    codeParams={codeParams}
                    onCodeParamsChange={setCodeParams}
                  />
                )}

                {source !== "composite" &&
                  visibleCodeParamEntries.length > 0 && (
                    <Box sx={{ mt: 2 }}>
                      <Typography variant="subtitle2" sx={{ mb: 1 }}>
                        Parameters
                      </Typography>
                      {visibleCodeParamEntries.map(([key, schema]) => (
                        <TextField
                          key={key}
                          fullWidth
                          size="small"
                          type={
                            schema?.type === "integer" ||
                            schema?.type === "number"
                              ? "number"
                              : "text"
                          }
                          label={key}
                          value={codeParams[key] ?? ""}
                          onChange={(e) => {
                            // BE's `type: number` schema rejects strings; coerce here.
                            const raw = e.target.value;
                            const isNumeric =
                              schema?.type === "integer" ||
                              schema?.type === "number";
                            let next = raw;
                            if (isNumeric && raw !== "") {
                              const n = Number(raw);
                              if (!Number.isNaN(n)) next = n;
                            }
                            handleCodeParamChange(key, next);
                          }}
                          helperText={
                            configParamsDesc?.[key] || schema?.description || ""
                          }
                          placeholder={
                            schema?.nullable
                              ? "optional"
                              : String(schema?.default ?? "")
                          }
                          sx={{ mb: 1 }}
                        />
                      ))}
                    </Box>
                  )}
              </Box>
            </Box>
          }
        />
      </Box>

      {isEditMode &&
        normalizedFullEval?.owner === "user" &&
        (source === "dataset" ||
          source === "experiment" ||
          source === "run-optimization") && (
          <Typography variant="caption" color="text.secondary">
            Saving settings can create a new template-wide default version.
          </Typography>
        )}

      {/* ── Bottom action bar (same as EvalDetailPage) ── */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          alignItems: "center",
          gap: 1,
          pt: 1.5,
          borderTop: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
          pb: 0.5,
        }}
      >
        {testError && (
          <Typography
            variant="caption"
            color="error.main"
            sx={{ mr: "auto", fontSize: "12px", maxWidth: 300 }}
            noWrap
          >
            {testError}
          </Typography>
        )}
        {testPassed && !testError && (
          <Box
            sx={{ display: "flex", alignItems: "center", gap: 0.5, mr: "auto" }}
          >
            <Iconify
              icon="mdi:check-circle"
              width={16}
              sx={{ color: "success.main" }}
            />
            <Typography
              variant="caption"
              color="success.main"
              sx={{ fontSize: "12px" }}
            >
              Test completed
            </Typography>
          </Box>
        )}
        {/* When composing a parent eval there's no dataset to map
            against, so we drop the "map all variables" requirement and
            let the user confirm the picked child with version + scoring
            settings only. */}
        {source !== "composite" &&
          !sourceReady &&
          !hasDataInjection &&
          !testError &&
          !testPassed && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ mr: "auto", fontSize: "11px" }}
            >
              Map all variables to enable testing & adding
            </Typography>
          )}
        {hasPendingChanges &&
          (bindingEdit || (sourceReady && !testPassed && !testError)) && (
            <Typography
              variant="caption"
              color="warning.main"
              sx={{ mr: "auto", fontSize: "11px" }}
            >
              Unsaved changes
            </Typography>
          )}

        {source !== "composite" &&
          source !== "workbench" &&
          source !== "create-simulate" &&
          // W7 (H-S10b): a remote scorer is tested on one row in Evals
          // (Edit in Evals); this panel's test has nothing to run for it.
          evalType !== "remote" &&
          (() => {
            // LLM evals may keep prompt content in User / Assistant turns
            // even if the System-derived instructions field is empty.
            const hasInstructions =
              !!(instructions || "").trim() ||
              (evalType === "llm" &&
                Array.isArray(messages) &&
                messages.some((m) => (m?.content || "").trim()));
            const hasVariables =
              Array.isArray(variables) && variables.length > 0;

            let testDisabled = false;
            let testDisabledReason = "";

            if (isTesting) {
              testDisabled = true;
              testDisabledReason = "Test is already running.";
            } else if (isComposite) {
              // Composite templates have no instructions/variables of their
              // own — they're driven by child evaluations + required_keys.
              // Skip the content checks; sourceReady below still applies.
            } else if (evalType === "code") {
              if (!(code || "").trim()) {
                testDisabled = true;
                testDisabledReason = "Write some code before running a test.";
              } else {
                const missingRequiredParam = visibleCodeParamEntries.find(
                  ([key, schema]) => {
                    if (!schema?.required) return false;
                    if (schema.nullable) return false;
                    if (schema.default !== null && schema.default !== undefined)
                      return false;
                    const v = codeParams[key];
                    return (
                      v === undefined ||
                      v === null ||
                      (typeof v === "string" && v.trim() === "")
                    );
                  },
                );
                if (missingRequiredParam) {
                  testDisabled = true;
                  testDisabledReason = `Set ${missingRequiredParam[0]} before running a test.`;
                }
              }
            } else if (!hasInstructions) {
              testDisabled = true;
              testDisabledReason = "Add instructions before running a test.";
            } else if (!hasVariables && !hasDataInjection) {
              testDisabled = true;
              testDisabledReason =
                templateFormat === "jinja"
                  ? "Your Jinja template has no variables. Reference an input with a {{ variable }} expression or a {% ... %} block (e.g. {{ input }}) so test input can be passed in."
                  : "Your Mustache template has no variables. Add a {{variable}} placeholder (e.g. {{input}}) so test input can be passed in.";
            }

            if (!testDisabled && !sourceReady && !hasDataInjection) {
              testDisabled = true;
              testDisabledReason = "Map all variables before running a test.";
            }

            return (
              <ShowComponent condition={!hasDataInjection}>
                <CustomTooltip
                  show={testDisabled && !!testDisabledReason}
                  type=""
                  title={testDisabledReason}
                  arrow
                >
                  <span>
                    <Button
                      variant="outlined"
                      color="primary"
                      size="small"
                      onClick={handleTestEvaluation}
                      disabled={testDisabled}
                      startIcon={
                        isTesting ? (
                          <CircularProgress size={14} />
                        ) : (
                          <Iconify icon="mdi:play-circle-outline" width={16} />
                        )
                      }
                      sx={{ textTransform: "none" }}
                    >
                      {isTesting ? "Testing..." : "Test Evaluation"}
                    </Button>
                  </span>
                </CustomTooltip>
              </ShowComponent>
            );
          })()}

        {(() => {
          // LLM evals may keep prompt content in User / Assistant turns
          // even if the System-derived instructions field is empty.
          const hasInstructions =
            !!(instructions || "").trim() ||
            (evalType === "llm" &&
              Array.isArray(messages) &&
              messages.some((m) => (m?.content || "").trim()));
          const hasVariables = Array.isArray(variables) && variables.length > 0;
          const actionLabel =
            source === "composite"
              ? "adding this evaluation to the composite"
              : isEditMode
                ? "updating this evaluation"
                : "adding this evaluation";

          let addDisabled = false;
          let addDisabledReason = "";

          if (isComposite || isTypedEvalType(evalType)) {
            // Composite templates have no instructions/variables of their
            // own — they're driven by child evaluations + required_keys.
            // W7 (H-S10b): a typed eval (a decision tree, a remote scorer)
            // is defined on its own tab in Evals, not by instructions here.
            // Skip the content checks; sourceReady below still applies.
          } else if (evalType === "code") {
            if (!(code || "").trim()) {
              addDisabled = true;
              addDisabledReason = `Write some code before ${actionLabel}.`;
            } else {
              // Mirror BE's required-param check so the round-trip never happens.
              const missingRequiredParam = visibleCodeParamEntries.find(
                ([key, schema]) => {
                  if (!schema?.required) return false;
                  if (schema.nullable) return false;
                  if (schema.default !== null && schema.default !== undefined)
                    return false;
                  const v = codeParams[key];
                  return (
                    v === undefined ||
                    v === null ||
                    (typeof v === "string" && v.trim() === "")
                  );
                },
              );
              if (missingRequiredParam) {
                addDisabled = true;
                addDisabledReason = `Set ${missingRequiredParam[0]} before ${actionLabel}.`;
              }
            }
          } else if (!hasInstructions) {
            addDisabled = true;
            addDisabledReason = `Add instructions before ${actionLabel}.`;
          } else if (!hasVariables && !hasDataInjection) {
            addDisabled = true;
            // A system eval's template can't be edited here (isSystemEval
            // locks it read-only), so with requireInputs on, telling the
            // user to add a {{variable}} to it is a dead end — say why the
            // add is actually blocked instead.
            addDisabledReason =
              requireInputs && isSystemEval
                ? "This evaluation has no inputs to map, so it can't run in an environment."
                : templateFormat === "jinja"
                  ? `Your Jinja template has no variables. Reference an input with a {{ variable }} expression or a {% ... %} block (e.g. {{ input }}) before ${actionLabel}.`
                  : `Your Mustache template has no variables. Add a {{variable}} placeholder (e.g. {{input}}) before ${actionLabel}.`;
          }

          if (!addDisabled && requireInputs && variables.length === 0) {
            addDisabled = true;
            addDisabledReason =
              "This evaluation has no inputs to map, so it can't run in an environment.";
          }

          if (
            !addDisabled &&
            source !== "composite" &&
            !sourceReady &&
            !hasDataInjection
          ) {
            addDisabled = true;
            addDisabledReason = `Map all variables before ${actionLabel}.`;
          }
          if (!addDisabled && decisionModel && decisionErrors.length) {
            addDisabled = true;
            addDisabledReason = decisionErrors[0];
          }

          if (!addDisabled && !canUpdate) {
            addDisabled = true;
            addDisabledReason = "No changes to save.";
          }

          return (
            <CustomTooltip
              show={addDisabled && !!addDisabledReason}
              type=""
              title={addDisabledReason}
              arrow
            >
              <span>
                <LoadingButton
                  variant="contained"
                  color="primary"
                  size="small"
                  loading={isSaving}
                  onClick={handleAdd}
                  disabled={addDisabled || !canUpdate}
                  sx={{ textTransform: "none" }}
                >
                  {primaryLabel ||
                    (source === "composite"
                      ? "Add to Composite"
                      : isEditMode
                        ? "Update Evaluation"
                        : "Add Evaluation")}
                </LoadingButton>
              </span>
            </CustomTooltip>
          );
        })()}
      </Box>
    </Box>
  );
};

EvalPickerConfigFull.propTypes = {
  evalData: PropTypes.object.isRequired,
  onBack: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  isSaving: PropTypes.bool,
  progress: PropTypes.node,
  primaryLabel: PropTypes.string,
  showClose: PropTypes.bool,
};

/**
 * W1: the evaluator picker's model menus list decision models (BYOK), and
 * the picker saves the decision question with the eval.
 */
const EvalPickerConfigFullWithDecisions = (props) => (
  <DecisionJudgesContext.Provider value>
    <EvalPickerConfigFull
      key={props.evalData?.userEvalId || props.evalData?.id}
      {...props}
    />
  </DecisionJudgesContext.Provider>
);
EvalPickerConfigFullWithDecisions.propTypes = EvalPickerConfigFull.propTypes;

export default EvalPickerConfigFullWithDecisions;

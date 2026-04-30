import {
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  IconButton,
  MenuItem,
  Select,
  Slider,
  TextField,
  Typography,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "notistack";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { FAGI_MODEL_VALUES } from "src/sections/evals/components/ModelSelector";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Iconify from "src/components/iconify";
import ResizablePanels from "src/components/resizablePanels/ResizablePanels";
import {
  useEvalDetail,
  useUpdateEval,
} from "src/sections/evals/hooks/useEvalDetail";
import {
  useEvalVersions,
  useCreateEvalVersion,
} from "src/sections/evals/hooks/useEvalVersions";
import InstructionEditor from "src/sections/evals/components/InstructionEditor";
import { extractJinjaVariables } from "src/utils/jinjaVariables";
import LLMPromptEditor from "src/sections/evals/components/LLMPromptEditor";
import CodeEvalEditor from "src/sections/evals/components/CodeEvalEditor";
import OutputTypeConfig from "src/sections/evals/components/OutputTypeConfig";
import FewShotExamples from "src/sections/evals/components/FewShotExamples";
import CompositeDetailPanel from "src/sections/evals/components/CompositeDetailPanel";
import { useCompositeDetail } from "src/sections/evals/hooks/useCompositeEval";
import { useCompositeChildrenUnionKeys } from "src/sections/evals/hooks/useCompositeChildrenKeys";
import DatasetTestMode from "src/sections/evals/components/DatasetTestMode";
import TracingTestMode from "src/sections/evals/components/TracingTestMode";
import SimulationTestMode from "src/sections/evals/components/SimulationTestMode";
import CreateSimulationPreviewMode from "src/sections/evals/components/CreateSimulationPreviewMode";
import TestPlayground from "src/sections/evals/components/TestPlayground";
import VersionBadge from "src/sections/evals/components/VersionBadge";
import EvalTypeBadge from "src/sections/evals/components/EvalTypeBadge";
import { useEvalPickerContext } from "./context/EvalPickerContext";
import {
  getEvalCode,
  getEvalCodeLanguage,
  getEvalConfigParamsDesc,
  getEvalFunctionParamsSchema,
  getEvalRequiredKeys,
  normalizeEvalPickerEval,
} from "./evalPickerValue";
import { getEvalBaseName } from "src/sections/common/EvaluationDrawer/common";
import { canonicalEntries } from "src/utils/utils";
import { format } from "date-fns";
import {
  buildEvalTemplateConfig,
  buildCompositeSourceModeProps,
  getSourceModeVariables,
  hasNonEmptyPromptMessage,
} from "./evalPickerConfigUtils";

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

const getEvalPromptText = (evalData, config = {}) =>
  evalData?.instructions || config?.rule_prompt || "";

const EvalPickerConfigFull = ({ evalData, onBack, onSave, isSaving }) => {
  const { isOSS } = useDeploymentMode();
  const {
    source,
    sourceId,
    sourceColumns,
    extraColumns,
    sourceRowType,
    sourcePreviewData,
    isEditMode,
    requiredColumnId,
  } = useEvalPickerContext();
  const normalizedEvalData = useMemo(
    () => normalizeEvalPickerEval(evalData),
    [evalData],
  );
  const templateId =
    evalData?.templateId || evalData?.template_id || evalData?.id;
  // ── Data (same hooks as EvalDetailPage) ──
  const { data: fullEval, isLoading, isError } = useEvalDetail(templateId);
  const normalizedFullEval = useMemo(
    () => normalizeEvalPickerEval(fullEval),
    [fullEval],
  );
  const { data: versionsData } = useEvalVersions(templateId);
  const versions = useMemo(() => versionsData?.versions || [], [versionsData]);
  const updateEval = useUpdateEval(templateId);
  const createVersion = useCreateEvalVersion(templateId);

  // ── Editable state (mirrors EvalDetailPage) ──
  const [selectedVersionId, setSelectedVersionId] = useState(null);
  const [instructions, setInstructions] = useState("");
  const [code, setCode] = useState("");
  const [codeLanguage, setCodeLanguage] = useState("python");
  const [model, setModel] = useState("turing_large");
  const [outputType, setOutputType] = useState("pass_fail");
  const [passThreshold, setPassThreshold] = useState(0.5);
  const [choiceScores, setChoiceScores] = useState({});
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
  // Name for the UserEvalMetric — defaults to template name, user can customise
  const [evalName, setEvalName] = useState("");
  const [dataReady, setDataReady] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [testPassed, setTestPassed] = useState(false);
  const [testError, setTestError] = useState(null);
  const [sourceReady, setSourceReady] = useState(false);
  const [promptMessageError, setPromptMessageError] = useState("");
  const [sourceMapping, setSourceMapping] = useState({});
  const sourceRef = useRef(null);

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
    normalizedFullEval?.evalType ||
    normalizedEvalData?.evalType ||
    "llm";
  const isSystemEval = normalizedFullEval?.owner === "system";
  const isComposite =
    (normalizedFullEval?.templateType || normalizedEvalData?.templateType) ===
    "composite";
  // Composite metadata + per-binding weight overrides. Loaded lazily via
  // `useCompositeDetail` only when the selected template is composite so
  // single-eval picks don't pay the round-trip cost. `compositeChildWeights`
  // is the state that flows into `composite_weight_overrides` on save.
  const { data: compositeDetail } = useCompositeDetail(templateId, isComposite);
  const compositeUnionKeys = useCompositeChildrenUnionKeys(
    compositeDetail?.children || [],
  );
  const [compositeChildWeights, setCompositeChildWeights] = useState({});
  const compositePopulatedRef = useRef(false);
  useEffect(() => {
    if (!isComposite) return;
    if (!compositeDetail) return;
    if (compositePopulatedRef.current) return;
    const initial = {};
    (compositeDetail.children || []).forEach((c) => {
      if (c?.child_id != null) {
        initial[c.child_id] = c.weight != null ? c.weight : 1.0;
      }
    });
    setCompositeChildWeights(initial);
    compositePopulatedRef.current = true;
  }, [isComposite, compositeDetail]);
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

  // Extract variables for the mapping panel. Priority:
  //   1. Composite: union of each child's required_keys (backend populates
  //      these on `CompositeDetailResponse.children[].required_keys`).
  //   2. required_keys on the template (authoritative for system evals)
  //   3. `{{var}}` patterns in the user-edited instructions
  // Note: code eval stdvars (input, output, expected) are always available
  // as positional args — they don't need explicit column mapping.
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
      return [];
    }

    // System evals + Jinja mode: use static required_keys.
    // Jinja's extractJinjaVariables() only returns root names (e.g. "file")
    // not full paths (e.g. "file.code"), so requiredKeys is authoritative.
    if ((isSystemEval || templateFormat === "jinja") && requiredKeys.length > 0) {
      return [...new Set(requiredKeys)];
    }

    // User evals (mustache): prefer live extraction so mapping updates as user types.
    const matches =
      (instructions || "").match(/\{\{\s*([^{}]+?)\s*\}\}/g) || [];
    const templateVars = matches.map((m) => m.replace(/\{\{|\}\}/g, "").trim());
    if (templateVars.length > 0) return [...new Set(templateVars)];
    // Fallback: stored required_keys (before instructions hydrate)
    if (requiredKeys.length > 0) return [...new Set(requiredKeys)];
    return [];
  }, [
    instructions,
    normalizedFullEval,
    normalizedEvalData,
    evalType,
    isComposite,
    isSystemEval,
    compositeDetail,
    templateFormat,
  ]);

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

  const sourceModeVariables = useMemo(
    () =>
      getSourceModeVariables({
        isComposite,
        variables,
        compositeUnionKeys,
      }),
    [isComposite, variables, compositeUnionKeys],
  );

  const hasValidPromptMessages = useMemo(
    () => evalType !== "llm" || hasNonEmptyPromptMessage(messages),
    [evalType, messages],
  );
  // ── Populate from eval detail ──
  // ── Populate from eval detail (same logic as EvalDetailPage) ──
  const initialLoadDone = useRef(false);
  useEffect(() => {
    // React Query can return cached detail first and fresh network detail next.
    // Re-hydrate while the form is still clean so fresh fields (e.g.
    // instructions/error_localizer_enabled) are not dropped.
    if (fullEval && (!initialLoadDone.current || !isDirty) && !selectedVersionId) {
      // Merge template config with any saved runtime overrides from the
      // user eval (run_config). Edit mode: evalData.run_config holds the
      // previously saved model, agentMode, passThreshold, etc.
      const rawRunConfig = evalData?.run_config || evalData?.runConfig || {};
      const normalizedRunConfig = {
        ...rawRunConfig,
        agent_mode: rawRunConfig.agent_mode ?? rawRunConfig.agentMode,
        check_internet:
          rawRunConfig.check_internet ?? rawRunConfig.checkInternet,
        knowledge_bases:
          rawRunConfig.knowledge_bases ?? rawRunConfig.knowledgeBases,
        data_injection:
          rawRunConfig.data_injection ?? rawRunConfig.dataInjection,
        template_format:
          rawRunConfig.template_format ?? rawRunConfig.templateFormat,
        few_shot_examples:
          rawRunConfig.few_shot_examples ?? rawRunConfig.fewShotExamples,
      };
      const config = {
        ...(fullEval.config || {}),
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
      const _type = normalizedFullEval?.evalType || normalizedEvalData?.evalType || "llm";
      if (_type === "code") {
        setInstructions("");
        setCode(getEvalCode(normalizedFullEval));
      } else {
        setInstructions(promptText);
        setCode("");
      }
      setCodeLanguage(getEvalCodeLanguage(normalizedFullEval));
      // Priority: user's saved run-config override → canonical detail
      // (`fullEval.model`, full form e.g. "turing_small") → list-level
      // `evalData.model`. The list endpoint returns a stripped form
      // ("small") for built-in templates while detail returns the full
      // canonical value, so we intentionally prefer `fullEval.model`
      // over `evalData.model` to avoid the chip rendering "small".
      setModel(
        config?.model || fullEval?.model || evalData?.model || ("turing_large"),
      );
      setOutputType(fullEval.output_type || "pass_fail");
      // Prefer user's saved run_config overrides (edit flow) over template defaults
      setPassThreshold(config.pass_threshold ?? fullEval.pass_threshold ?? 0.5);
      setChoiceScores(config.choice_scores || fullEval.choice_scores || {});
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
      const di = config.data_injection;
      if (di?.full_row || di?.fullRow) setContextOptions(["full_row"]);
      setErrorLocalizerEnabled(
        config.error_localizer_enabled ??
          fullEval.error_localizer_enabled ??
          false,
      );

      // Edit mode: keep the saved name. Create mode: generate a unique name
      // from the template base name + current date (e.g. "is_good_summary_14_Apr_2026").
      if (isEditMode) {
        setEvalName(evalData?.name || fullEval.name || "");
      } else {
        const baseName =
          fullEval.name || normalizedEvalData?.name || getEvalBaseName(fullEval);
        const sanitized = baseName
          .toLowerCase()
          .replace(/\s+/g, "_")
          .replace(/[^a-z0-9_-]/g, "");
        const dateSuffix = format(new Date(), "dd_MMM_yyyy").toLowerCase();
        setEvalName(`${sanitized}_${dateSuffix}`);
      }

      initialLoadDone.current = true;
      setDataReady(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    fullEval,
    normalizedFullEval,
    normalizedEvalData,
    isEditMode,
    selectedVersionId,
  ]);

  // ── Version selection ──
  const handleVersionChange = useCallback(
    (e) => {
      const vId = e.target.value;
      setSelectedVersionId(vId || null);
      if (!vId && fullEval) {
        // Type-aware split — see initial-load effect above.
        const _type =
          normalizedFullEval?.evalType ||
          normalizedEvalData?.evalType ||
          "llm";
        const promptText = getEvalPromptText(fullEval, fullEval.config || {});
        if (_type === "code") {
          setInstructions("");
          setCode(getEvalCode(normalizedFullEval));
        } else {
          setInstructions(promptText);
          setCode("");
        }
        setModel(fullEval.config?.model || fullEval.model || ("turing_large"));
        if (fullEval.config?.messages?.length > 0) {
          setMessages(fullEval.config.messages);
        } else if (_type === "llm" && promptText) {
          setMessages([{ role: "system", content: promptText }]);
        }
        setAgentMode(fullEval.config?.agent_mode || "agent");
        setSummaryType(fullEval.config?.summary?.type || "concise");
        if (Array.isArray(fullEval.config?.tools)) {
          setConnectorIds(fullEval.config.tools);
        } else if (
          fullEval.config?.tools &&
          typeof fullEval.config.tools === "object"
        ) {
          setConnectorIds(
            Object.entries(fullEval.config.tools)
              .filter(([, enabled]) => !!enabled)
              .map(([name]) => name),
          );
        } else {
          setConnectorIds([]);
        }
        setKnowledgeBaseIds(
          Array.isArray(fullEval.config?.knowledge_bases)
            ? fullEval.config.knowledge_bases
            : [],
        );
        setContextOptions(
          fullEval.config?.data_injection?.full_row ||
            fullEval.config?.data_injection?.fullRow
            ? ["full_row"]
            : ["variables_only"],
        );
        setUseInternet(fullEval.config?.check_internet ?? false);
        setErrorLocalizerEnabled(
          fullEval.error_localizer_enabled ??
            fullEval.config?.error_localizer_enabled ??
            false,
        );
        setIsDirty(false);
        return;
      }
      const version = versions.find((v) => v.id === vId);
      if (version) {
        const config = version.config_snapshot || version.configSnapshot || {};
        const promptText = version.criteria || config.rule_prompt || "";
        // Type-aware split — version snapshot's `criteria` is the code
        // text for code evals, the prompt for agent/llm.
        const _type =
          normalizedFullEval?.evalType ||
          normalizedEvalData?.evalType ||
          "llm";
        if (_type === "code") {
          setInstructions("");
          setCode(config.code || "");
        } else {
          setInstructions(promptText);
          setCode("");
        }
        setModel(config.model || ("turing_large"));
        if (config.messages?.length > 0) {
          setMessages(config.messages);
        } else if (_type === "llm" && promptText) {
          setMessages([{ role: "system", content: promptText }]);
        }
        setAgentMode(config.agent_mode || "agent");
        setSummaryType(config.summary?.type || config.summary || "concise");
        if (Array.isArray(config.tools)) {
          setConnectorIds(config.tools);
        } else if (config.tools && typeof config.tools === "object") {
          setConnectorIds(
            Object.entries(config.tools)
              .filter(([, enabled]) => !!enabled)
              .map(([name]) => name),
          );
        } else {
          setConnectorIds([]);
        }
        setKnowledgeBaseIds(
          Array.isArray(config.knowledge_bases) ? config.knowledge_bases : [],
        );
        setContextOptions(
          config.data_injection?.full_row || config.data_injection?.fullRow
            ? ["full_row"]
            : ["variables_only"],
        );
        setUseInternet(config.check_internet ?? false);
        setIsDirty(false);
      }
    },
    [versions, fullEval, normalizedFullEval, normalizedEvalData],
  );

  // ── Save as new version (user evals only) ──
  // Mirrors EvalDetailPage.handleSaveVersion: updates the template fields,
  // then creates a new EvalTemplateVersion with a config snapshot. After
  // save, the new version becomes the selected version in the tabs so
  // the user can Apply it to the dataset with the existing
  // "Add Evaluation" button.
  const handleSaveVersion = useCallback(async () => {
    if (isSystemEval) return;
    if (isOSS && evalType === "agent") {
      enqueueSnackbar(
        "Agent evaluations are not available on OSS. Use LLM-as-a-Judge or Code evaluations instead.",
        { variant: "error" },
      );
      return;
    }
    if (isOSS && FAGI_MODEL_VALUES.has(model)) {
      enqueueSnackbar(
        "Turing models are not available in OSS. Please select your own model.",
        { variant: "error" },
      );
      return;
    }
    try {
      const payload = {
        instructions: evalType === "code" ? "" : instructions,
        code: evalType === "code" ? code : undefined,
        code_language: evalType === "code" ? codeLanguage : undefined,
        model,
        output_type: outputType,
        pass_threshold: passThreshold,
        choice_scores:
          Object.keys(choiceScores || {}).length > 0 ? choiceScores : null,
        messages: evalType === "llm" ? messages : undefined,
        few_shot_examples:
          evalType === "llm" && fewShotExamples.length > 0
            ? fewShotExamples
            : undefined,
      };
      await updateEval.mutateAsync(payload);

      const configSnapshot = {
        ...(fullEval?.config || evalData?.config || {}),
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
        messages: evalType === "llm" ? messages : undefined,
        few_shot_examples:
          evalType === "llm" && fewShotExamples.length > 0
            ? fewShotExamples
            : undefined,
      };
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
        setSelectedVersionId(newVersion.id);
      }
    } catch (err) {
      const message =
        err?.response?.data?.result || err?.message || "Failed to save version";
      enqueueSnackbar(
        typeof message === "string" ? message : JSON.stringify(message),
        { variant: "error" },
      );
    }
  }, [
    isSystemEval,
    isOSS,
    evalType,
    instructions,
    code,
    codeLanguage,
    model,
    outputType,
    passThreshold,
    choiceScores,
    messages,
    fewShotExamples,
    fullEval,
    evalData,
    updateEval,
    createVersion,
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
    setIsTesting(true);
    setTestError(null);
    setTestPassed(false);
    sourceRef.current?.runTest?.(templateId);
    // Safety timeout
    setTimeout(() => setIsTesting((v) => (v ? false : v)), 60000);
  }, [templateId]);

  const handleAdd = useCallback(() => {
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

    // Build a data_injection config from context options. "variables_only"
    // means only use template variables; any other option enables full_row.
    const dataInjection =
      contextOptions.length === 0 ||
      (contextOptions.length === 1 && contextOptions[0] === "variables_only")
        ? { variables_only: true }
        : { full_row: true };
    const tools = build_tools_payload(connectorIds);

    const templateType =
      fullEval?.template_type ||
      fullEval?.templateType ||
      evalData?.templateType;

    const resolvedConfig = buildEvalTemplateConfig({
      baseConfig: fullEval?.config || evalData?.config || {},
      evalType,
      instructions,
      code,
      codeLanguage,
      messages,
      fewShotExamples,
      outputType,
      passThreshold,
      choiceScores,
      templateFormat,
    });

    // When the picker was opened in edit mode (initialEval came from a
    // saved-evals row or the column-menu Edit Eval action), evalData
    // carries `userEvalId` — the existing UserEvalMetric id. The host
    // (EvaluationDrawer) reads this to route to /edit_and_run_user_eval
    // instead of /add_user_eval, preventing duplicate bindings.
    const userEvalId = evalData?.userEvalId;

    // In edit mode: evalData.name is the saved instance name — skip
    // fullEval.name (template name) so it never overwrites the instance name.
    const resolvedName = isEditMode
      ? evalName || evalData?.name
      : evalName || fullEval?.name || evalData?.name;

    if (templateType === "composite") {
      // Composite metrics don't carry prompt/model/output-type/choice-score
      // state — those live on each child template. Emit only the fields
      // the host needs to create a UserEvalMetric plus the per-binding
      // weight overrides.
      onSave({
        templateId,
        evalTemplateId: templateId,
        userEvalId,
        name: resolvedName,
        mapping: sourceMapping,
        evalTemplate: fullEval || evalData,
        evalType,
        templateType,
        config: fullEval?.config || evalData?.config,
        versionId: selectedVersionId,
        data_injection: dataInjection,
        error_localizer_enabled: errorLocalizerEnabled,
        composite_weight_overrides: compositeChildWeights,
      });
      return;
    }

    onSave({
      templateId,
      evalTemplateId: templateId,
      userEvalId,
      name: resolvedName,
      model,
      mapping: sourceMapping,
      evalTemplate: fullEval || evalData,
      evalType,
      templateType,
      outputType,
      config: resolvedConfig,
      versionId: selectedVersionId,
      instructions,
      messages,
      pass_threshold: passThreshold,
      choice_scores: choiceScores,
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
      error_localizer_enabled: errorLocalizerEnabled,
    });
  }, [
    templateId,
    fullEval,
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
    codeParams,
    agentMode,
    useInternet,
    summaryType,
    connectorIds,
    knowledgeBaseIds,
    contextOptions,
    compositeChildWeights,
    errorLocalizerEnabled,
    hasValidPromptMessages,
    templateFormat,
    onSave,
    requiredColumnId,
    sourceColumns,
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
        <Typography variant="body2" color="text.secondary">
          {!templateId
            ? "No template ID found for this evaluation."
            : "Failed to load evaluation details."}
        </Typography>
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
          <VersionBadge version={currentVersion} />
          <EvalTypeBadge type={evalType} />
          {isDirty && (
            <Chip
              label="Edited"
              size="small"
              color="warning"
              variant="outlined"
              sx={{ fontSize: "10px", height: 18 }}
            />
          )}
        </Box>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          {/* Save-as-new-version — user evals only. System evals are
              read-only by product policy (editing triggers Copy flow).
              Sits beside the existing version dropdown so the user can
              edit, save a new version, then pick it from the dropdown
              and click Add Evaluation. */}
          {!isSystemEval && !isComposite && (
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
          {versions.length > 0 && (
            <Select
              size="small"
              value={selectedVersionId || ""}
              onChange={handleVersionChange}
              displayEmpty
              sx={{ fontSize: "12px", minWidth: 130, height: 30 }}
            >
              <MenuItem value="" sx={{ fontSize: "12px" }}>
                Default version
              </MenuItem>
              {versions.map((v) => (
                <MenuItem key={v.id} value={v.id} sx={{ fontSize: "12px" }}>
                  V{v.version_number}
                  {v.is_default ? " (default)" : ""}
                </MenuItem>
              ))}
            </Select>
          )}
        </Box>
      </Box>

      {/* ── Eval Name ── */}
      <Box sx={{ py: 1.5, flexShrink: 0 }}>
        <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
          Name<span style={{ color: "#d32f2f" }}>*</span>
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
          inputProps={{ maxLength: 60 }}
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
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            p: 1,
                            borderRadius: 1,
                            border: "1px solid",
                            borderColor: "divider",
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

              {/* Agent type — only render after data loads so Quill
                  initializes with the actual instructions content */}
              {!isComposite && evalType === "agent" && dataReady && (
                <InstructionEditor
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
                />
              )}

              {/* LLM type. ModelSelector is rendered inline in
                  LLMPromptEditor's top bar (alongside the template
                  format selector) so LLM-as-a-judge has the same
                  top-bar layout as the agent InstructionEditor. */}
              {!isComposite && evalType === "llm" && dataReady && (
                <>
                  <LLMPromptEditor
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
              {isComposite ? null : evalType === "code" ? (
                <Box>
                  <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
                    Scoring
                  </Typography>
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 2,
                    }}
                  >
                    <Typography variant="caption">0</Typography>
                    <Slider
                      value={passThreshold * 100}
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
                  passThreshold={passThreshold}
                  onPassThresholdChange={(v) => {
                    setPassThreshold(v);
                    setIsDirty(true);
                  }}
                  disabled={false}
                  categoryLocked={isOutputTypeCategoryLocked}
                />
              )}

              {/* Error Localization (single-eval concern, LLM/Agent only).
                  Code evals don't support error localization — the feature
                  introspects model traces, which code evals don't produce. */}
              {!isComposite && evalType !== "code" && (
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={errorLocalizerEnabled}
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
              {/* Source label */}
              <Typography
                variant="body2"
                fontWeight={600}
                sx={{ mb: 1.5, fontSize: "13px" }}
              >
                {source === "composite"
                  ? "Test Playground"
                  : `${SOURCE_LABELS[source] || "Preview"} — Variable Mapping`}
              </Typography>

              {/* Render the source-specific component directly (no tabs) */}
              <Box sx={{ flex: 1, overflow: "auto", pb: 2 }}>
                {(source === "dataset" ||
                  source === "experiment" ||
                  source === "workbench" ||
                  source === "run-experiment" ||
                  source === "run-optimization") && (
                  <DatasetTestMode
                    ref={sourceRef}
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
                    errorLocalizerEnabled={errorLocalizerEnabled}
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
                    templateId={templateId}
                    variables={sourceModeVariables}
                    codeParams={codeParams}
                    onTestResult={handleTestResult}
                    onClearResult={handleClearTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    onReadyChange={handleSourceReadyChange}
                    errorLocalizerEnabled={errorLocalizerEnabled}
                    initialMapping={evalData?.mapping}
                    {...compositeSourceModeProps}
                  />
                )}
                {(source === "simulation" || source === "test") && (
                  <SimulationTestMode
                    ref={sourceRef}
                    templateId={templateId}
                    variables={sourceModeVariables}
                    codeParams={codeParams}
                    onTestResult={handleTestResult}
                    onClearResult={handleClearTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    onReadyChange={handleSourceReadyChange}
                    errorLocalizerEnabled={errorLocalizerEnabled}
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
                  />
                )}
                {source === "task" && (
                  <TracingTestMode
                    ref={sourceRef}
                    templateId={templateId}
                    variables={sourceModeVariables}
                    codeParams={codeParams}
                    onTestResult={handleTestResult}
                    onClearResult={handleClearTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    onReadyChange={handleSourceReadyChange}
                    initialProjectId={sourceId}
                    initialRowType={sourceRowType}
                    initialMapping={evalData?.mapping}
                    errorLocalizerEnabled={errorLocalizerEnabled}
                    {...compositeSourceModeProps}
                  />
                )}
                {source === "custom" && (
                  <DatasetTestMode
                    ref={sourceRef}
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
                    errorLocalizerEnabled={errorLocalizerEnabled}
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
                    instructions={evalType === "code" ? "" : instructions}
                    evalType={evalType}
                    requiredKeys={variables}
                    showVersions={!(isSystemEval && evalType === "code")}
                    onTestResult={handleTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    isComposite={false}
                    model={model}
                    functionParamsSchema={functionParamsSchema}
                    configParamsDesc={configParamsDesc}
                  />
                )}

                {source !== "composite" && visibleCodeParamEntries.length > 0 && (
                  <Box sx={{ mt: 2 }}>
                    <Typography variant="body2" fontWeight={600} sx={{ mb: 1 }}>
                      Parameters
                    </Typography>
                    {visibleCodeParamEntries.map(([key, schema]) => (
                      <TextField
                        key={key}
                        fullWidth
                        size="small"
                        type={
                          schema?.type === "integer" || schema?.type === "number"
                            ? "number"
                            : "text"
                        }
                        label={key}
                        value={codeParams[key] ?? ""}
                        onChange={(e) => handleCodeParamChange(key, e.target.value)}
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
          !testError &&
          !testPassed && (
            <Typography
              variant="caption"
              color="text.disabled"
              sx={{ mr: "auto", fontSize: "11px" }}
            >
              Map all variables to enable testing & adding
            </Typography>
          )}
        {isDirty && sourceReady && !testPassed && !testError && (
          <Typography
            variant="caption"
            color="warning.main"
            sx={{ mr: "auto", fontSize: "11px" }}
          >
            Unsaved changes
          </Typography>
        )}

        {source !== "composite" && source !== "workbench" && (() => {
          const hasInstructions = !!(instructions || "").trim();
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
            }
          } else if (!hasInstructions) {
            testDisabled = true;
            testDisabledReason = "Add instructions before running a test.";
          } else if (!hasVariables) {
            testDisabled = true;
            testDisabledReason =
              templateFormat === "jinja"
                ? "Your Jinja template has no variables. Reference an input with a {{ variable }} expression or a {% ... %} block (e.g. {{ input }}) so test input can be passed in."
                : "Your Mustache template has no variables. Add a {{variable}} placeholder (e.g. {{input}}) so test input can be passed in.";
          }

          if (!testDisabled && !sourceReady) {
            testDisabled = true;
            testDisabledReason = "Map all variables before running a test.";
          }

          return (
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
          );
        })()}

        {(() => {
          const hasInstructions = !!(instructions || "").trim();
          const hasVariables =
            Array.isArray(variables) && variables.length > 0;
          const actionLabel =
            source === "composite"
              ? "adding this evaluation to the composite"
              : isEditMode
                ? "updating this evaluation"
                : "adding this evaluation";

          let addDisabled = false;
          let addDisabledReason = "";

          if (isComposite) {
            // Composite templates have no instructions/variables of their
            // own — they're driven by child evaluations + required_keys.
            // Skip the content checks; sourceReady below still applies.
          } else if (evalType === "code") {
            if (!(code || "").trim()) {
              addDisabled = true;
              addDisabledReason = `Write some code before ${actionLabel}.`;
            }
          } else if (!hasInstructions) {
            addDisabled = true;
            addDisabledReason = `Add instructions before ${actionLabel}.`;
          } else if (!hasVariables) {
            addDisabled = true;
            addDisabledReason =
              templateFormat === "jinja"
                ? `Your Jinja template has no variables. Reference an input with a {{ variable }} expression or a {% ... %} block (e.g. {{ input }}) before ${actionLabel}.`
                : `Your Mustache template has no variables. Add a {{variable}} placeholder (e.g. {{input}}) before ${actionLabel}.`;
          }

          // Non-composite flows additionally require the full source config
          // (name / output type / etc.) to be valid.
          if (!addDisabled && source !== "composite" && !sourceReady) {
            addDisabled = true;
            addDisabledReason = `Map all variables before ${actionLabel}.`;
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
                  disabled={addDisabled}
                  sx={{ textTransform: "none" }}
                >
                  {source === "composite"
                    ? "Add to Composite"
                    : isEditMode
                      ? "Update Evaluation"
                      : "Add Evaluation"}
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
};

export default EvalPickerConfigFull;

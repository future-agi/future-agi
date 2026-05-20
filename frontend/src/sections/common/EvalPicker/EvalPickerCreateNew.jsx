import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  Slider,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
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
import { useSnackbar } from "notistack";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

// Same components as EvalCreatePage
import { useCreateEval } from "src/sections/evals/hooks/useCreateEval";
import { useUpdateEval } from "src/sections/evals/hooks/useEvalDetail";
import { useCreateCompositeEval } from "src/sections/evals/hooks/useCompositeEval";
import ModelSelector, { FAGI_MODEL_VALUES } from "src/sections/evals/components/ModelSelector";
import InstructionEditor from "src/sections/evals/components/InstructionEditor";
import { extractJinjaVariables } from "src/utils/jinjaVariables";
import LLMPromptEditor from "src/sections/evals/components/LLMPromptEditor";
import CodeEvalEditor from "src/sections/evals/components/CodeEvalEditor";
import OutputTypeConfig from "src/sections/evals/components/OutputTypeConfig";
import FewShotExamples from "src/sections/evals/components/FewShotExamples";
import CompositeDetailPanel from "src/sections/evals/components/CompositeDetailPanel";
import TestPlayground from "src/sections/evals/components/TestPlayground";
import { useCompositeChildrenUnionKeys } from "src/sections/evals/hooks/useCompositeChildrenKeys";
import DatasetTestMode from "src/sections/evals/components/DatasetTestMode";
import TracingTestMode from "src/sections/evals/components/TracingTestMode";
import SimulationTestMode from "src/sections/evals/components/SimulationTestMode";
import { useEvalPickerContext } from "./context/EvalPickerContext";
import {
  contextOptionsForRowType,
  extractCodeEvaluateParams,
} from "./evalPickerConfigUtils";

const TRACING_ROW_TYPE_TO_KEY = {
  Span: "spans",
  Trace: "traces",
  Session: "sessions",
  VoiceCall: "voiceCalls",
};

const dataInjectionFromContextOptions = (opts) => {
  if (
    !opts ||
    opts.length === 0 ||
    (opts.length === 1 && opts[0] === "variables_only")
  ) {
    return { variables_only: true };
  }
  const flags = {};
  if (opts.includes("dataset_row")) flags.full_row = true;
  if (opts.includes("span_context")) flags.span_context = true;
  if (opts.includes("trace_context")) flags.trace_context = true;
  if (opts.includes("session_context")) flags.session_context = true;
  if (opts.includes("call_context")) flags.call_context = true;
  return Object.keys(flags).length > 0 ? flags : { variables_only: true };
};

const PYTHON_CODE_TEMPLATE = `from typing import Any

def evaluate(input: Any, output: Any, expected: Any, context: dict, **kwargs):
    # Your evaluation logic here
    return {"score": 1.0, "reason": "Evaluation passed"}
`;

const EVAL_TYPE_TABS = [
  { value: "agent", label: "Agents" },
  { value: "llm", label: "LLM-As-A-Judge" },
  { value: "code", label: "Code" },
];

// Top-level mode toggle — mirrors EvalCreatePage so the drawer and the
// full page offer the same composite affordance. Composite lives under
// its own mode rather than as a flat 4th eval-type tab because its
// config surface is structurally different (no model / prompt / code,
// child picker + weights instead).
const MODE_TABS = [
  { value: "single", label: "Single" },
  { value: "composite", label: "Composite" },
];

const SOURCE_LABELS = {
  dataset: "Dataset",
  tracing: "Tracing",
  simulation: "Simulation",
  task: "Task",
  custom: "Custom",
};

const EvalPickerCreateNew = ({ onBack, onSave }) => {
  const {
    source,
    sourceId,
    sourceRowType,
    sourceColumns,
    setSelectedEval,
    setStep,
    onFiltersChange,
    filterForm: localFilterForm,
  } = useEvalPickerContext();
  const { enqueueSnackbar } = useSnackbar();
  const { isOSS } = useDeploymentMode();
  const createEval = useCreateEval();
  const createComposite = useCreateCompositeEval();
  const sourceRef = useRef(null);

  // Form state (same as EvalCreatePage)
  const [name, setName] = useState("");
  const [mode, setMode] = useState("single");
  const [evalType, setEvalType] = useState("agent");
  const [instructions, setInstructions] = useState("");
  const [code, setCode] = useState(PYTHON_CODE_TEMPLATE);
  const [codeLanguage, setCodeLanguage] = useState("python");
  const [model, setModel] = useState("turing_large");
  const [outputType, setOutputType] = useState("pass_fail");
  const [passThreshold, setPassThreshold] = useState(0.5);
  const [choiceScores, setChoiceScores] = useState({});
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState([]);
  const [fewShotExamples, setFewShotExamples] = useState([]);
  const [messages, setMessages] = useState([{ role: "system", content: "" }]);
  const [templateFormat, setTemplateFormat] = useState("mustache");
  const [datasetColumns, setDatasetColumns] = useState([]);
  const [datasetJsonSchemas, setDatasetJsonSchemas] = useState({});
  const [contextOptions, setContextOptions] = useState(
    () => contextOptionsForRowType(sourceRowType) || ["variables_only"],
  );


  const handleSourceRowTypeChange = useCallback((rt) => {
    const map =  TRACING_ROW_TYPE_TO_KEY;
    const key = map[rt];
    const seeded = key ? contextOptionsForRowType(key) : null;
    if (seeded) setContextOptions(seeded);
  }, []);

  const localFormFilters = useWatch({
    control: localFilterForm.control,
    name: "filters",
  });
  const localApiFilters = useMemo(
    () => buildApiFilterArray(localFormFilters),
    [localFormFilters],
  );

  // Composite eval state (only used when evalType === "composite")
  const [selectedChildren, setSelectedChildren] = useState([]);
  const [childWeights, setChildWeights] = useState({});
  const [aggregationEnabled, setAggregationEnabled] = useState(true);
  const [aggregationFunction, setAggregationFunction] =
    useState("weighted_avg");
  const [compositeChildAxis, setCompositeChildAxis] = useState("pass_fail");
  // Union of every child template's required_keys — drives the top
  // TestPlayground so the user sees inputs for all child variables.
  const compositeUnionKeys = useCompositeChildrenUnionKeys(selectedChildren);
  const compositeAdhocConfig = useMemo(
    () =>
      mode !== "composite"
        ? null
        : {
            child_template_ids: selectedChildren.map((c) => c.child_id),
            aggregation_enabled: aggregationEnabled,
            aggregation_function: aggregationFunction,
            composite_child_axis: compositeChildAxis || "",
            child_weights:
              Object.keys(childWeights || {}).length > 0 ? childWeights : null,
            pass_threshold: passThreshold ?? 0.5,
          },
    [
      mode,
      selectedChildren,
      aggregationEnabled,
      aggregationFunction,
      compositeChildAxis,
      childWeights,
      passThreshold,
    ],
  );

  // Draft management
  const [draftId, setDraftId] = useState(null);
  const updateDraft = useUpdateEval(draftId);
  const draftCreating = useRef(false);

  // Test state
  const [isTesting, setIsTesting] = useState(false);
  const [testPassed, setTestPassed] = useState(false);
  const [testError, setTestError] = useState(null);
  const [sourceReady, setSourceReady] = useState(false);
  const [sourceMapping, setSourceMapping] = useState({});
  const [isSaving, setIsSaving] = useState(false);
  // Inline field-level validation errors. Cleared on the corresponding
  // field edit so the form feels responsive rather than nagging.
  const [errors, setErrors] = useState({});

  const handleColumnsLoaded = useCallback((cols, jsonSchemas) => {
    setDatasetColumns(cols || []);
    setDatasetJsonSchemas(jsonSchemas || {});
  }, []);

  const handleSourceReadyChange = useCallback(
    (ready, mapping) => {
      setSourceReady(ready);
      if (mapping) setSourceMapping(mapping);
      if (ready && errors.mapping)
        setErrors((prev) => ({ ...prev, mapping: undefined }));
    },
    [errors.mapping],
  );

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

  // Create draft on mount.
  // Must include `is_draft: true` — the backend EvalTemplateCreateV2View
  // validates `instructions`, name format and uniqueness on non-draft
  // creates. Without the flag we get a "Instructions are required" 400
  // the moment the user clicks "Create New Eval", before they've typed
  // anything. EvalCreatePage uses the same flag for the same reason.
  useEffect(() => {
    if (draftCreating.current || draftId) return;
    draftCreating.current = true;
    (async () => {
      try {
        const data = await createEval.mutateAsync({
          is_draft: true,
          eval_type: "agent",
          output_type: "pass_fail",
          model: "turing_large",
          pass_threshold: 0.5,
        });
        if (data?.id) setDraftId(data.id);
      } catch {
        // silent
      }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-save to draft
  const buildPayload = useCallback(
    () => ({
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
          ? fewShotExamples.map((ds) => ({ id: ds.id, name: ds.name }))
          : undefined,
      template_format: templateFormat,
      data_injection:
        evalType === "agent"
          ? dataInjectionFromContextOptions(contextOptions)
          : undefined,
    }),
    [
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
      templateFormat,
      contextOptions,
    ],
  );

  const autoSaveTimer = useRef(null);
  const skipFirst = useRef(true);
  useEffect(() => {
    if (!draftId) return;
    if (skipFirst.current) {
      skipFirst.current = false;
      return;
    }
    if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
    autoSaveTimer.current = setTimeout(() => {
      updateDraft.mutate(buildPayload());
    }, 800);
    return () => {
      if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
    };
  }, [draftId, buildPayload]); // eslint-disable-line react-hooks/exhaustive-deps

  // Test
  const handleTestEvaluation = useCallback(async () => {
    if (!draftId) return;
    setIsTesting(true);
    setTestError(null);
    setTestPassed(false);
    try {
      await updateDraft.mutateAsync(buildPayload());
      sourceRef.current?.runTest?.(draftId);
      setTimeout(() => setIsTesting((v) => (v ? false : v)), 60000);
    } catch (error) {
      handleTestResult(false, error?.message || "Failed to test");
    }
  }, [draftId, buildPayload, updateDraft, handleTestResult]);

  const hasDataInjection = useMemo(
    () =>
      evalType === "agent" &&
      (source === "task" || source === "tracing") &&
      Array.isArray(contextOptions) &&
      contextOptions.some((o) => o && o !== "variables_only"),
    [evalType, source, contextOptions],
  );

  // Validate all required fields for single-eval mode. Composite mode has
  // its own light validation (name + at least one child) inside
  // handleSaveAndAddComposite, so this gate is only applied to the
  // single path. Returns true if valid; sets inline errors and returns
  // false otherwise.
  const validate = useCallback(() => {
    const next = {};

    // Name rules
    if (!name.trim()) {
      next.name = "Eval name is required";
    } else if (!/^[a-z0-9_-]+$/.test(name)) {
      next.name =
        "Name can only contain lowercase letters, numbers, underscores, and hyphens";
    } else if (/^[-_]|[-_]$/.test(name)) {
      next.name =
        "Name cannot start or end with hyphens (-) or underscores (_)";
    } else if (/_-|-_/.test(name)) {
      next.name = "Name cannot contain consecutive separators (_- or -_)";
    }

    // Instructions / code rules
    if (evalType === "code") {
      if (!code.trim()) next.instructions = "Code is required";
    } else if (!instructions.trim()) {
      next.instructions = "Instructions are required";
    } else if (
      !hasDataInjection &&
      !/\{\{\s*[^{}]+?\s*\}\}/.test(instructions)
    ) {
      next.instructions =
        "Instructions must contain at least one template variable (e.g. {{input}})";
    }

    if (!sourceReady && source !== "composite" && !hasDataInjection) {
      next.mapping = "Map all variables before saving";
    }

    // pass_threshold must be 0–1
    if (passThreshold < 0 || passThreshold > 1) {
      next.passThreshold = "pass_threshold must be between 0 and 1";
    }

    // choice_scores required and valid for deterministic output
    if (outputType === "deterministic") {
      if (!choiceScores || Object.keys(choiceScores).length === 0) {
        next.choiceScores =
          "choice_scores is required when output_type is 'deterministic'";
      } else {
        const invalid = Object.entries(choiceScores).find(
          ([k, v]) => !k.trim() || typeof v !== "number" || v < 0 || v > 1,
        );
        if (invalid) {
          next.choiceScores = `Choice '${invalid[0]}' score must be between 0 and 1`;
        }
      }
    }

    setErrors(next);
    return Object.keys(next).length === 0;
  }, [
    name,
    evalType,
    code,
    instructions,
    sourceReady,
    source,
    passThreshold,
    outputType,
    choiceScores,
    hasDataInjection,
  ]);

  // Field-change wrappers — set the value and clear the corresponding
  // inline error so the user sees immediate feedback when they fix a
  // field flagged by validate().
  const handleInstructionsChange = useCallback(
    (val) => {
      setInstructions(val);
      if (errors.instructions)
        setErrors((prev) => ({ ...prev, instructions: undefined }));
    },
    [errors.instructions],
  );

  const handlePassThresholdChange = useCallback(
    (val) => {
      setPassThreshold(val);
      if (errors.passThreshold)
        setErrors((prev) => ({ ...prev, passThreshold: undefined }));
    },
    [errors.passThreshold],
  );

  const handleChoiceScoresChange = useCallback(
    (val) => {
      setChoiceScores(val);
      if (errors.choiceScores)
        setErrors((prev) => ({ ...prev, choiceScores: undefined }));
    },
    [errors.choiceScores],
  );

  const handleOutputTypeChange = useCallback(
    (val) => {
      setOutputType(val);
      if (errors.choiceScores)
        setErrors((prev) => ({ ...prev, choiceScores: undefined }));
    },
    [errors.choiceScores],
  );

  // Save & Add
  const handleSaveAndAdd = useCallback(async () => {
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
    if (!validate()) return;
    if (!draftId) {
      enqueueSnackbar("Draft not ready, please wait a moment", {
        variant: "warning",
      });
      return;
    }
    setIsSaving(true);
    try {
      await updateDraft.mutateAsync({
        name: name.trim(),
        ...buildPayload(),
        description: description || null,
        tags,
        publish: true,
      });
      if (source === "task" && onFiltersChange) {
        onFiltersChange(localFilterForm.getValues("filters") || []);
      }
      // Now add to the current context. data_injection (seeded from
      // sourceRowType) is forwarded so the consumer's serializeEvalConfig
      // captures it inside config.run_config — same shape an existing
      // eval would emit through EvalPickerConfigFull.
      onSave({
        templateId: draftId,
        evalTemplateId: draftId,
        name: name.trim(),
        model,
        mapping: sourceMapping,
        evalType,
        outputType,
        instructions,
        data_injection:
          evalType === "agent"
            ? dataInjectionFromContextOptions(contextOptions)
            : undefined,
      });
    } catch (error) {
      enqueueSnackbar(error?.message || "Failed to save", { variant: "error" });
    } finally {
      setIsSaving(false);
    }
  }, [
    validate,
    draftId,
    name,
    description,
    tags,
    buildPayload,
    updateDraft,
    onSave,
    model,
    sourceMapping,
    evalType,
    outputType,
    instructions,
    contextOptions,
    enqueueSnackbar,
    isOSS,
    source,
    onFiltersChange,
    localFilterForm,
  ]);

  // Save & Add — composite branch. Composite templates are created via
  // `POST eval_templates/composite`, then we hand off to the existing
  // EvalPickerConfigFull screen so the user can map the composite's
  // child variables to dataset columns. Without this hand-off the eval
  // would be added with an empty mapping and silently fail at run time.
  const handleSaveAndAddComposite = useCallback(async () => {
    if (!name.trim()) {
      enqueueSnackbar("Enter an eval name", { variant: "warning" });
      return;
    }
    if (selectedChildren.length === 0) {
      enqueueSnackbar("Pick at least one child evaluation", {
        variant: "warning",
      });
      return;
    }
    setIsSaving(true);
    try {
      const childIds = selectedChildren.map((c) => c.child_id || c.id);
      const weights = childIds.reduce((acc, id) => {
        if (childWeights[id] != null) acc[id] = childWeights[id];
        return acc;
      }, {});
      const result = await createComposite.mutateAsync({
        name: name.trim(),
        description: description || null,
        child_template_ids: childIds,
        aggregation_enabled: aggregationEnabled,
        aggregation_function: aggregationFunction,
        composite_child_axis: compositeChildAxis,
        child_weights: Object.keys(weights).length > 0 ? weights : null,
      });
      // Route into the config step so the user maps the union of child
      // required_keys to dataset columns. EvalPickerConfigFull handles
      // composite templates natively (loads composite detail, shows
      // weights, etc.) and on its own Save button forwards through to
      // the parent's onSave (→ /addEval).
      setSelectedEval({
        id: result?.id,
        name: name.trim(),
        templateType: "composite",
        evalType: result?.eval_type || "llm",
        outputType:
          compositeChildAxis === "percentage" ? "percentage" : "pass_fail",
      });
      setStep("config");
    } catch (error) {
      enqueueSnackbar(
        error?.response?.data?.result ||
          error?.message ||
          "Failed to create composite evaluation",
        { variant: "error" },
      );
    } finally {
      setIsSaving(false);
    }
  }, [
    name,
    description,
    selectedChildren,
    childWeights,
    aggregationEnabled,
    aggregationFunction,
    compositeChildAxis,
    createComposite,
    setSelectedEval,
    setStep,
    enqueueSnackbar,
  ]);

  const isComposite = mode === "composite";
  // `source === "composite"` means this drawer was opened from a composite's
  // child picker with no dataset bound — there's no variable mapping to
  // complete here, so don't gate saving on `sourceReady`.
  const canSave = isComposite
    ? !!name.trim() && selectedChildren.length > 0
    : name.trim() &&
      (evalType === "code" ? code.trim() : instructions.trim()) &&
      (source === "composite" || sourceReady || hasDataInjection);

  // Variables from instructions
  const variables = useMemo(() => {
    if (evalType === "code") {
      // Live-parse the user's `def evaluate(...)` signature so adding /
      // renaming a parameter immediately surfaces a new mapping row.
      const liveParams = extractCodeEvaluateParams(code, codeLanguage);
      if (liveParams.length > 0) return [...new Set(liveParams)];
      return ["input", "output", "expected"];
    }
    if (!instructions) return [];
    let vars;
    if (templateFormat === "jinja") {
      vars = extractJinjaVariables(instructions || "");
    } else {
      const matches =
        (instructions || "").match(/\{\{\s*([^{}]+?)\s*\}\}/g) || [];
      vars = matches.map((m) => m.replace(/\{\{|\}\}/g, "").trim());
    }
    return [...new Set(vars)];
  }, [instructions, evalType, templateFormat, code, codeLanguage]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          pb: 1.5,
          flexShrink: 0,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <IconButton size="small" onClick={onBack} sx={{ p: 0.5 }}>
            <Iconify icon="solar:arrow-left-linear" width={18} />
          </IconButton>
          <Typography variant="subtitle1" fontWeight={600}>
            Create New Evaluation
          </Typography>
          {draftId && (
            <Typography
              variant="caption"
              sx={{
                fontFamily: "monospace",
                fontSize: "10px",
                color: "text.disabled",
                bgcolor: "action.hover",
                px: 0.75,
                py: 0.25,
                borderRadius: "4px",
              }}
            >
              Draft
            </Typography>
          )}
        </Box>
      </Box>

      <Divider />

      {/* Two-panel layout */}
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
                height: "100%",
                overflow: "auto",
              }}
            >
              {/* Single / Composite mode toggle — same UX as EvalCreatePage */}
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <Typography variant="subtitle1" fontWeight={600}>
                  Eval details
                </Typography>
                <Tabs
                  value={mode}
                  onChange={(_, val) => setMode(val)}
                  TabIndicatorProps={{ style: { display: "none" } }}
                  sx={{
                    minHeight: 28,
                    "& .MuiTab-root": {
                      minHeight: 28,
                      px: 1.5,
                      py: 0,
                      mr: "0px !important",
                      textTransform: "none",
                      fontSize: "13px",
                      borderRadius: "6px",
                    },
                    border: "1px solid",
                    borderColor: "divider",
                    p: "2px",
                    borderRadius: "8px",
                    bgcolor: (theme) =>
                      theme.palette.mode === "dark"
                        ? "rgba(255,255,255,0.04)"
                        : "background.neutral",
                  }}
                >
                  {MODE_TABS.map((tab) => (
                    <Tab
                      key={tab.value}
                      value={tab.value}
                      label={tab.label}
                      sx={{
                        bgcolor:
                          mode === tab.value
                            ? (theme) =>
                                theme.palette.mode === "dark"
                                  ? "rgba(255,255,255,0.12)"
                                  : "background.paper"
                            : "transparent",
                        boxShadow:
                          mode === tab.value
                            ? (theme) =>
                                theme.palette.mode === "dark"
                                  ? "none"
                                  : "0 1px 3px rgba(0,0,0,0.08)"
                            : "none",
                        borderRadius: "6px",
                        fontWeight: mode === tab.value ? 600 : 400,
                        color:
                          mode === tab.value ? "text.primary" : "text.disabled",
                      }}
                    />
                  ))}
                </Tabs>
              </Box>

              {/* Eval Name — CompositeDetailPanel has its own name field,
                  so we hide this for composite to avoid two inputs writing
                  to the same state. */}
              {!isComposite && (
                <Box>
                  <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
                    Eval Name*
                  </Typography>
                  <TextField
                    fullWidth
                    size="small"
                    placeholder="e.g. hallucination_detector"
                    value={name}
                    error={!!errors.name}
                    helperText={
                      errors.name ||
                      "Lowercase, numbers, underscores, hyphens only"
                    }
                    onChange={(e) => {
                      setName(
                        e.target.value
                          .toLowerCase()
                          .replace(/\s+/g, "_")
                          .replace(/[^a-z0-9_-]/g, ""),
                      );
                      if (errors.name)
                        setErrors((prev) => ({ ...prev, name: undefined }));
                    }}
                  />
                </Box>
              )}

              {/* Composite branch: full child picker + weights + aggregation */}
              {isComposite && (
                <CompositeDetailPanel
                  editable
                  name={name}
                  description={description}
                  aggregationEnabled={aggregationEnabled}
                  aggregationFunction={aggregationFunction}
                  compositeChildAxis={compositeChildAxis}
                  childWeights={childWeights}
                  children={selectedChildren}
                  // Forward the source context so the inner child
                  // picker shows the variable-mapping screen for each
                  // child against the same source the parent composite
                  // was opened from (task, dataset, tracing, ...).
                  pickerSource={source}
                  pickerSourceId={sourceId}
                  pickerSourceRowType={sourceRowType}
                  pickerSourceColumns={sourceColumns}
                  pickerSourceFilters={localFormFilters}
                  pickerOnFiltersChange={(f) =>
                    localFilterForm.setValue("filters", f || [])
                  }
                  onNameChange={setName}
                  onDescriptionChange={setDescription}
                  onAggregationEnabledChange={setAggregationEnabled}
                  onAggregationFunctionChange={setAggregationFunction}
                  onCompositeChildAxisChange={setCompositeChildAxis}
                  onChildrenChange={setSelectedChildren}
                  onChildWeightsChange={setChildWeights}
                />
              )}

              {/* Single branch: eval-type tabs + the matching editor */}
              {!isComposite && (
                <Tabs
                  value={evalType}
                  onChange={(_, val) => setEvalType(val)}
                  TabIndicatorProps={{ style: { display: "none" } }}
                  sx={{
                    minHeight: 28,
                    "& .MuiTab-root": {
                      minHeight: 28,
                      px: 1.5,
                      py: 0,
                      mr: "0px !important",
                      textTransform: "none",
                      fontSize: "13px",
                      borderRadius: "6px",
                    },
                    border: "1px solid",
                    borderColor: "divider",
                    p: "2px",
                    borderRadius: "8px",
                    width: "fit-content",
                    bgcolor: (theme) =>
                      theme.palette.mode === "dark"
                        ? "rgba(255,255,255,0.04)"
                        : "background.neutral",
                  }}
                >
                  {EVAL_TYPE_TABS.map((tab) => (
                    <Tab
                      key={tab.value}
                      value={tab.value}
                      label={tab.label}
                      sx={{
                        bgcolor:
                          evalType === tab.value
                            ? (theme) =>
                                theme.palette.mode === "dark"
                                  ? "rgba(255,255,255,0.12)"
                                  : "background.paper"
                            : "transparent",
                        boxShadow:
                          evalType === tab.value
                            ? (theme) =>
                                theme.palette.mode === "dark"
                                  ? "none"
                                  : "0 1px 3px rgba(0,0,0,0.08)"
                            : "none",
                        borderRadius: "6px",
                        fontWeight: evalType === tab.value ? 600 : 400,
                        color:
                          evalType === tab.value
                            ? "text.primary"
                            : "text.disabled",
                      }}
                    />
                  ))}
                </Tabs>
              )}

              {/* Agent type */}
              {!isComposite && evalType === "agent" && (
                <>
                  <InstructionEditor
                    value={instructions}
                    onChange={handleInstructionsChange}
                    model={model}
                    onModelChange={setModel}
                    templateFormat={templateFormat}
                    onTemplateFormatChange={setTemplateFormat}
                    datasetColumns={datasetColumns}
                    datasetJsonSchemas={datasetJsonSchemas}
                    mappedVariables={sourceMapping}
                    activeContextOptions={contextOptions}
                    onActiveContextOptionsChange={setContextOptions}
                  />
                  {errors.instructions && (
                    <Typography variant="caption" color="error.main">
                      {errors.instructions}
                    </Typography>
                  )}
                </>
              )}

              {/* LLM type */}
              {!isComposite && evalType === "llm" && (
                <>
                  <ModelSelector
                    model={model}
                    onModelChange={setModel}
                    showMode={false}
                    showPlus={false}
                  />
                  <LLMPromptEditor
                    messages={messages}
                    onMessagesChange={(msgs) => {
                      setMessages(msgs);
                      const sysMsg = msgs.find((m) => m.role === "system");
                      if (sysMsg) setInstructions(sysMsg.content);
                    }}
                    templateFormat={templateFormat}
                    onTemplateFormatChange={setTemplateFormat}
                    datasetColumns={datasetColumns}
                    datasetJsonSchemas={datasetJsonSchemas}
                  />
                  <FewShotExamples
                    selectedDatasets={fewShotExamples}
                    onChange={setFewShotExamples}
                  />
                </>
              )}

              {/* Code type */}
              {!isComposite && evalType === "code" && (
                <>
                  <CodeEvalEditor
                    code={code}
                    setCode={setCode}
                    codeLanguage={codeLanguage}
                    setCodeLanguage={setCodeLanguage}
                    datasetColumns={datasetColumns}
                  />
                  {errors.instructions && (
                    <Typography variant="caption" color="error.main">
                      {errors.instructions}
                    </Typography>
                  )}
                </>
              )}

              {/* Output Type — not shown for composite (CompositeDetailPanel
                  carries its own aggregation + child-axis controls) */}
              {!isComposite &&
                (evalType === "code" ? (
                  <Box>
                    <Typography
                      variant="body2"
                      fontWeight={600}
                      sx={{ mb: 0.5 }}
                    >
                      Pass Threshold
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
                        value={passThreshold * 100}
                        onChange={(_, val) =>
                          handlePassThresholdChange(val / 100)
                        }
                        min={0}
                        max={100}
                        size="small"
                        valueLabelDisplay="auto"
                        valueLabelFormat={(v) => `${Math.round(v)}%`}
                      />
                      <Typography variant="caption">100%</Typography>
                    </Box>
                    {errors.passThreshold && (
                      <Typography variant="caption" color="error.main">
                        {errors.passThreshold}
                      </Typography>
                    )}
                  </Box>
                ) : (
                  <>
                    <OutputTypeConfig
                      outputType={outputType}
                      onOutputTypeChange={handleOutputTypeChange}
                      choiceScores={choiceScores}
                      onChoiceScoresChange={handleChoiceScoresChange}
                      passThreshold={passThreshold}
                      onPassThresholdChange={handlePassThresholdChange}
                    />
                    {errors.choiceScores && (
                      <Typography variant="caption" color="error.main">
                        {errors.choiceScores}
                      </Typography>
                    )}
                    {errors.passThreshold && (
                      <Typography variant="caption" color="error.main">
                        {errors.passThreshold}
                      </Typography>
                    )}
                  </>
                ))}

              {/* Description — CompositeDetailPanel already has its own */}
              {!isComposite && (
                <Box>
                  <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
                    Description
                  </Typography>
                  <TextField
                    fullWidth
                    size="small"
                    multiline
                    minRows={2}
                    placeholder="What does this eval check?"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                  />
                </Box>
              )}

            </Box>
          }
          rightPanel={
            <Box
              sx={{
                pl: 2,
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
                  {`${SOURCE_LABELS[source] || "Preview"} — ${isComposite ? "Composite Test" : "Variable Mapping"}`}
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
              <Box sx={{ flex: 1, overflow: "auto" }}>
                {(source === "dataset" ||
                  source === "workbench" ||
                  source === "custom" ||
                  source === "run-experiment" ||
                  source === "run-optimization") && (
                  <DatasetTestMode
                    ref={sourceRef}
                    templateId={draftId}
                    variables={isComposite ? compositeUnionKeys : variables}
                    model={model}
                    onTestResult={handleTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    initialDatasetId={sourceId}
                    onReadyChange={handleSourceReadyChange}
                    isComposite={isComposite}
                    compositeAdhocConfig={compositeAdhocConfig}
                    sourceColumns={
                      source === "workbench" ? sourceColumns : null
                    }
                  />
                )}
                {source === "task" && (
                  <TracingTestMode
                    ref={sourceRef}
                    templateId={draftId}
                    variables={isComposite ? compositeUnionKeys : variables}
                    onTestResult={handleTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    onReadyChange={handleSourceReadyChange}
                    initialProjectId={sourceId}
                    initialRowType={sourceRowType}
                    isComposite={isComposite}
                    compositeAdhocConfig={compositeAdhocConfig}
                    localFilters={localApiFilters}
                  />
                )}
                {source === "tracing" && (
                  <TracingTestMode
                    ref={sourceRef}
                    templateId={draftId}
                    variables={isComposite ? compositeUnionKeys : variables}
                    onTestResult={handleTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    onReadyChange={handleSourceReadyChange}
                    onRowTypeChange={handleSourceRowTypeChange}
                    isComposite={isComposite}
                    compositeAdhocConfig={compositeAdhocConfig}
                  />
                )}
                {(source === "simulation" ||
                  source === "test" ||
                  source === "create-simulate") && (
                  <SimulationTestMode
                    ref={sourceRef}
                    templateId={draftId}
                    variables={isComposite ? compositeUnionKeys : variables}
                    onTestResult={handleTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    onReadyChange={handleSourceReadyChange}
                    isComposite={isComposite}
                    compositeAdhocConfig={compositeAdhocConfig}
                  />
                )}
                {/* Fallback: no source context (standalone composite create page) */}
                {isComposite &&
                  !source &&
                  ![
                    "dataset",
                    "workbench",
                    "task",
                    "custom",
                    "run-experiment",
                    "run-optimization",
                    "tracing",
                    "simulation",
                    "test",
                    "create-simulate",
                  ].includes(source) && (
                    <TestPlayground
                      ref={sourceRef}
                      templateId={draftId}
                      instructions=""
                      evalType="llm"
                      requiredKeys={compositeUnionKeys}
                      isComposite
                      compositeAdhocConfig={compositeAdhocConfig}
                      showVersions={false}
                      onTestResult={handleTestResult}
                      onColumnsLoaded={handleColumnsLoaded}
                    />
                  )}
              </Box>
            </Box>
          }
        />
      </Box>

      {/* Bottom action bar */}
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
        {!sourceReady && !hasDataInjection && !testError && !testPassed && (
          <Typography
            variant="caption"
            color="text.disabled"
            sx={{ mr: "auto", fontSize: "11px" }}
          >
            Map all variables to enable{" "}
            {source === "workbench" ? "saving" : "testing & saving"}
          </Typography>
        )}

        <ShowComponent
          condition={!hasDataInjection }
        >
          <Button
            variant="outlined"
            size="small"
            onClick={handleTestEvaluation}
            disabled={
              isTesting ||
              (!sourceReady && !hasDataInjection) ||
              !draftId ||
              isComposite ||
              source === "workbench"
            }
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
        </ShowComponent>

        <LoadingButton
          variant="contained"
          size="small"
          loading={isSaving}
          disabled={!canSave}
          onClick={isComposite ? handleSaveAndAddComposite : handleSaveAndAdd}
          sx={{ textTransform: "none" }}
        >
          {isComposite ? "Create & Configure" : "Save & Add Evaluation"}
        </LoadingButton>
      </Box>
    </Box>
  );
};

EvalPickerCreateNew.propTypes = {
  onBack: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
};

export default EvalPickerCreateNew;

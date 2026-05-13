import {
  Box,
  Breadcrumbs,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  FormControlLabel,
  Link,
  Slider,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import React, { useCallback, useEffect, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { useNavigate, useParams } from "react-router";
import { useSnackbar } from "notistack";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { FAGI_MODEL_VALUES } from "./ModelSelector";

import { useCreateEval } from "../hooks/useCreateEval";
import { useUpdateEval } from "../hooks/useEvalDetail";
import { useCreateCompositeEval } from "../hooks/useCompositeEval";
import FewShotExamples from "./FewShotExamples";
import InstructionEditor from "./InstructionEditor";
import LLMPromptEditor from "./LLMPromptEditor";
import OutputTypeConfig from "./OutputTypeConfig";
import ResizablePanels from "src/components/resizablePanels/ResizablePanels";
import TestPlayground from "./TestPlayground";
import { useCompositeChildrenUnionKeys } from "../hooks/useCompositeChildrenKeys";
import CodeEditor from "./CodeEditor";
import CodeEvalEditor, {
  PYTHON_CODE_TEMPLATE,
  JS_CODE_TEMPLATE,
} from "./CodeEvalEditor";
import CompositeDetailPanel from "./CompositeDetailPanel";
import UnsavedChangesDialog from "src/sections/projects/MonitorsView/UnsavedChangesDialog";
import { extractVariables } from "src/utils/utils";

const EVAL_TYPE_TABS = [
  { value: "agent", label: "Agents" },
  { value: "llm", label: "LLM-As-A-Judge" },
  { value: "code", label: "Code" },
];

const EVAL_TAGS = [
  {
    value: "red_teaming",
    label: "Red Teaming",
    icon: "mdi:shield-alert-outline",
  },
  {
    value: "retrieval_systems",
    label: "Retrieval Systems",
    icon: "mdi:database-search-outline",
  },
  {
    value: "harmful_objects",
    label: "Harmful Objects",
    icon: "mdi:alert-octagon-outline",
  },
  {
    value: "chatbot_behaviors",
    label: "Chatbot behaviors",
    icon: "mdi:robot-outline",
  },
  {
    value: "output_format",
    label: "Output Format",
    icon: "mdi:format-list-bulleted",
  },
  { value: "nlp_metrics", label: "NLP Metrics", icon: "mdi:chart-bar" },
  { value: "data_leakage", label: "Data Leakage", icon: "mdi:leak" },
  {
    value: "output_validation",
    label: "Output Validation",
    icon: "mdi:check-decagram-outline",
  },
  { value: "image", label: "Image", icon: "mdi:image-outline" },
  { value: "audio", label: "Audio", icon: "mdi:volume-high" },
  { value: "medical", label: "Medical", icon: "mdi:medical-bag" },
  { value: "finance", label: "Finance", icon: "mdi:currency-usd" },
  { value: "agents", label: "Agents", icon: "mdi:robot-excited-outline" },
];

const extractSelectedTools = (tools) => {
  if (Array.isArray(tools)) return tools;
  if (tools && typeof tools === "object") {
    return Object.entries(tools)
      .filter(([, enabled]) => !!enabled)
      .map(([name]) => name);
  }
  return [];
};

const buildToolsPayload = (selectedTools) =>
  (selectedTools || []).reduce((acc, toolName) => {
    if (toolName) acc[toolName] = true;
    return acc;
  }, {});

const resolveSummaryType = (summary) => {
  if (summary && typeof summary === "object" && summary.type) {
    return summary.type;
  }
  if (typeof summary === "string" && summary.trim()) return summary;
  return "concise";
};

const resolveContextOptions = (dataInjection) => {
  if (!dataInjection || typeof dataInjection !== "object") {
    return ["variables_only"];
  }
  const opts = [];
  if (dataInjection.full_row || dataInjection.fullRow) opts.push("dataset_row");
  if (dataInjection.span_context || dataInjection.spanContext) opts.push("span_context");
  if (dataInjection.trace_context || dataInjection.traceContext) opts.push("trace_context");
  if (dataInjection.session_context || dataInjection.sessionContext) opts.push("session_context");
  if (dataInjection.call_context || dataInjection.callContext) opts.push("call_context");
  if (opts.length > 0) return opts;
  if (dataInjection.variables_only === false || dataInjection.variablesOnly === false) {
    return ["full_row"];
  }
  return ["variables_only"];
};

const EvalCreatePage = () => {
  const { draftId: urlDraftId } = useParams();
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const { isOSS } = useDeploymentMode();
  const createEval = useCreateEval();
  const createComposite = useCreateCompositeEval();
  const testPlaygroundRef = useRef(null);

  // Mode: single or composite
  const [mode, setMode] = useState("single");

  // --- Single eval state ---
  const [name, setName] = useState("");
  const [evalType, setEvalType] = useState("agent");
  const [instructions, setInstructions] = useState("");
  const [code, setCode] = useState(PYTHON_CODE_TEMPLATE);
  const [codeLanguage, setCodeLanguage] = useState("python");
  const [model, setModel] = useState("turing_large");
  const [outputType, setOutputType] = useState("pass_fail");
  const [passThreshold, setPassThreshold] = useState(0.5);
  const [choiceScores, setChoiceScores] = useState({});
  const [multiChoice, setMultiChoice] = useState(false);
  const [description, setDescription] = useState("");
  const [checkInternet, setCheckInternet] = useState(false);
  const [agentMode, setAgentMode] = useState("agent");
  const [summaryType, setSummaryType] = useState("concise");
  const [customSummary, setCustomSummary] = useState("");
  const [connectorIds, setConnectorIds] = useState([]);
  const [knowledgeBaseIds, setKnowledgeBaseIds] = useState([]);
  const [contextOptions, setContextOptions] = useState(["variables_only"]);
  const [errorLocalizerEnabled, setErrorLocalizerEnabled] = useState(false);
  const [tags, setTags] = useState([]);
  const [fewShotExamples, setFewShotExamples] = useState([]);
  const [messages, setMessages] = useState([{ role: "system", content: "" }]);
  const [templateFormat, setTemplateFormat] = useState("mustache");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [datasetColumns, setDatasetColumns] = useState([]);
  const [datasetJsonSchemas, setDatasetJsonSchemas] = useState({});

  // Callback from DatasetTestMode → TestPlayground → here
  const handleColumnsLoaded = useCallback((cols, jsonSchemas) => {
    setDatasetColumns(cols || []);
    setDatasetJsonSchemas(jsonSchemas || {});
  }, []);

  // --- Composite eval state ---
  const [compositeName, setCompositeName] = useState("");
  const [compositeDescription, setCompositeDescription] = useState("");
  // Track full child objects (not just IDs) so we can show names/types
  // in the panel without relying on a separate eval-list lookup.
  const [selectedChildren, setSelectedChildren] = useState([]);
  // Union of every child template's `required_keys`. Drives the
  // TestPlayground in composite mode so the user sees one input per
  // variable across all children.
  const compositeUnionKeys = useCompositeChildrenUnionKeys(selectedChildren);
  const [aggregationEnabled, setAggregationEnabled] = useState(true);
  const [aggregationFunction, setAggregationFunction] =
    useState("weighted_avg");
  const [childWeights, setChildWeights] = useState({}); // { childId: weight }
  const [compositeChildAxis, setCompositeChildAxis] = useState("pass_fail");

  const [testPassed, setTestPassed] = useState(false);
  const [testError, setTestError] = useState(null);
  const [draftId, setDraftId] = useState(urlDraftId || null);
  const [isTesting, setIsTesting] = useState(false);
  const draftCreating = useRef(false);

  // Warn before switching modes if there's in-flight work we'd lose.
  const [pendingMode, setPendingMode] = useState(null);

  // Test run epoch: increments whenever a test starts OR is invalidated by a
  // mode switch. `activeTestEpochRef` is the epoch of the currently-armed test.
  // If they differ when a result arrives, the result is stale and ignored —
  // prevents a late response from the old mode surfacing in the new mode.
  const testEpochRef = useRef(0);
  const activeTestEpochRef = useRef(0);

  // Hook for updating the draft template
  const updateDraft = useUpdateEval(draftId);

  const handleTestResult = useCallback((success, result) => {
    // Stale result from a test that was invalidated by a mode switch — drop it.
    if (activeTestEpochRef.current !== testEpochRef.current) return;
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

  // Load existing draft from URL, or create a new one
  const draftLoaded = useRef(false);
  useEffect(() => {
    if (draftCreating.current) return;

    // If URL has a draft ID, load its config
    if (urlDraftId && !draftLoaded.current) {
      draftLoaded.current = true;
      (async () => {
        try {
          const { data } = await axios.get(
            endpoints.develop.eval.getEvalDetail(urlDraftId),
          );
          const d = data?.result;
          if (d) {
            const config = d.config || {};
            setEvalType(d.eval_type || "agent");
            setInstructions(d.instructions || "");
            setCode(config.code || d.code || PYTHON_CODE_TEMPLATE);
            setCodeLanguage(config.language || "python");
            setModel(config.model || d.model || ("turing_large"));
            setOutputType(d.output_type_normalized || "pass_fail");
            setPassThreshold(d.pass_threshold ?? 0.5);
            setChoiceScores(d.choice_scores || {});
            setMultiChoice(d.multi_choice ?? config.multi_choice ?? false);
            setDescription(d.description || "");
            setCheckInternet(config.check_internet ?? false);
            setAgentMode(config.agent_mode || "agent");
            setSummaryType(resolveSummaryType(config.summary));
            setCustomSummary(config.summary?.custom || "");
            setConnectorIds(extractSelectedTools(config.tools));
            setKnowledgeBaseIds(
              Array.isArray(config.knowledge_bases)
                ? config.knowledge_bases
                : [],
            );
            setContextOptions(resolveContextOptions(config.data_injection));
            setErrorLocalizerEnabled(
              d.error_localizer_enabled ||
                config.error_localizer_enabled ||
                false,
            );
            setTags(d.eval_tags || []);
            setTemplateFormat(
              d.template_format || config.template_format || "mustache",
            );
            if (config.messages) setMessages(config.messages);
            if (config.few_shot_examples) {
              setFewShotExamples(config.few_shot_examples || []);
            }
          }
        } catch {
          // Draft not found — create a new one
          setDraftId(null);
        }
      })();
      return;
    }

    // No URL draft — create a new one
    if (!draftId) {
      draftCreating.current = true;
      (async () => {
        try {
          const { data } = await axios.post(
            endpoints.develop.eval.createEvalTemplateV2,
            {
              is_draft: true,
              eval_type: "agent",
              output_type: "pass_fail",
              model: "turing_large",
              pass_threshold: 0.5,
            },
          );
          const id = data?.result?.id;
          if (id) {
            setDraftId(id);
            navigate(`/dashboard/evaluations/create/${id}`, { replace: true });
          }
        } catch {
          // ignore — user can retry
        }
      })();
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-save config to draft (debounced, skip initial load)
  const autoSaveTimer = useRef(null);
  const autoSaveSkipFirst = useRef(!!urlDraftId); // skip first trigger when loading existing draft
  const buildUpdatePayload = useCallback(() => {
    const dataInjection = (() => {
      if (
        contextOptions.length === 0 ||
        (contextOptions.length === 1 && contextOptions[0] === "variables_only")
      ) {
        return { variables_only: true };
      }
      // Send individual flags so the backend can enable the right tools
      const flags = {};
      if (contextOptions.includes("dataset_row")) flags.full_row = true;
      if (contextOptions.includes("span_context")) flags.span_context = true;
      if (contextOptions.includes("trace_context")) flags.trace_context = true;
      if (contextOptions.includes("session_context")) flags.session_context = true;
      if (contextOptions.includes("call_context")) flags.call_context = true;
      // If nothing specific matched, default to full_row for backward compat
      return Object.keys(flags).length > 0 ? flags : { full_row: true };
    })();

    const summary =
      summaryType === "custom"
        ? { type: "custom", custom: customSummary }
        : { type: summaryType };

    const tools = buildToolsPayload(connectorIds);

    return {
      eval_type: evalType,
      instructions:
        evalType === "code"
          ? ""
          : evalType === "llm"
            ? instructions ||
              messages.find((m) => m.role === "system")?.content ||
              undefined
            : instructions || undefined,
      code: evalType === "code" ? code : undefined,
      code_language: evalType === "code" ? codeLanguage : undefined,
      model,
      output_type: outputType,
      pass_threshold: passThreshold,
      choice_scores:
        Object.keys(choiceScores || {}).length > 0 ? choiceScores : null,
      multi_choice: multiChoice,
      check_internet: checkInternet,
      mode: evalType === "agent" ? agentMode : undefined,
      tools: evalType === "agent" ? tools : undefined,
      knowledge_bases: evalType === "agent" ? knowledgeBaseIds : undefined,
      data_injection: evalType === "agent" ? dataInjection : undefined,
      summary: evalType === "agent" ? summary : undefined,
      error_localizer_enabled: errorLocalizerEnabled,
      messages: evalType === "llm" ? messages : undefined,
      few_shot_examples:
        evalType === "llm" && fewShotExamples.length > 0
          ? fewShotExamples.map((ds) => ({ id: ds.id, name: ds.name }))
          : undefined,
      template_format: templateFormat,
    };
  }, [
    evalType,
    instructions,
    code,
    codeLanguage,
    model,
    outputType,
    passThreshold,
    choiceScores,
    multiChoice,
    checkInternet,
    agentMode,
    summaryType,
    customSummary,
    connectorIds,
    knowledgeBaseIds,
    contextOptions,
    errorLocalizerEnabled,
    messages,
    fewShotExamples,
    templateFormat,
  ]);

  useEffect(() => {
    if (!draftId) return;
    if (autoSaveSkipFirst.current) {
      autoSaveSkipFirst.current = false;
      return;
    }
    if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
    autoSaveTimer.current = setTimeout(() => {
      updateDraft.mutate(buildUpdatePayload());
    }, 800);
    return () => {
      if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
    };
  }, [draftId, buildUpdatePayload]); // eslint-disable-line react-hooks/exhaustive-deps

  // Draft cleanup: drafts with visible_ui=False are hidden from the list.
  // No auto-delete — stale drafts can be cleaned up by a backend cron.
  const publishedRef = useRef(false);

  // --- Save handlers ---
  const handleSaveSingle = useCallback(async () => {
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
    if (!draftId) {
      enqueueSnackbar("Draft not ready yet, please try again", {
        variant: "warning",
      });
      return;
    }
    try {
      // Publish the draft: set name, mark visible
      await updateDraft.mutateAsync({
        name: name.trim(),
        ...buildUpdatePayload(),
        description: description || null,
        tags,
        publish: true,
      });
      publishedRef.current = true;
      enqueueSnackbar("Evaluation saved successfully", { variant: "success" });
      navigate(`/dashboard/evaluations/${draftId}`);
    } catch (error) {
      const message =
        error?.response?.data?.result ||
        error?.message ||
        "Failed to save evaluation";
      enqueueSnackbar(
        typeof message === "string" ? message : JSON.stringify(message),
        {
          variant: "error",
        },
      );
    }
  }, [
    draftId,
    name,
    description,
    tags,
    buildUpdatePayload,
    updateDraft,
    enqueueSnackbar,
    navigate,
    isOSS,
    model,
  ]);

  const handleSaveComposite = useCallback(async () => {
    try {
      // Build child_weights only for selected children; backend defaults to 1.0 if missing.
      const childIds = selectedChildren.map((c) => c.child_id);
      const weights = childIds.reduce((acc, id) => {
        if (childWeights[id] != null) {
          acc[id] = childWeights[id];
        }
        return acc;
      }, {});
      const payload = {
        name: compositeName.trim(),
        description: compositeDescription || null,
        child_template_ids: childIds,
        aggregation_enabled: aggregationEnabled,
        aggregation_function: aggregationFunction,
        composite_child_axis: compositeChildAxis,
        child_weights: Object.keys(weights).length > 0 ? weights : null,
      };
      const result = await createComposite.mutateAsync(payload);
      enqueueSnackbar("Composite evaluation created successfully", {
        variant: "success",
      });
      navigate(`/dashboard/evaluations/${result.id}`);
    } catch (error) {
      const message =
        error?.response?.data?.result ||
        error?.message ||
        "Failed to create composite evaluation";
      enqueueSnackbar(
        typeof message === "string" ? message : JSON.stringify(message),
        {
          variant: "error",
        },
      );
    }
  }, [
    compositeName,
    compositeDescription,
    selectedChildren,
    aggregationEnabled,
    aggregationFunction,
    compositeChildAxis,
    childWeights,
    createComposite,
    enqueueSnackbar,
    navigate,
  ]);

  // Test Evaluation: draft is always auto-saved, just run it
  const handleTestEvaluation = useCallback(async () => {
    // Composite tests run via the adhoc endpoint — no draft persistence
    // needed since the composite hasn't been (and won't be) saved as a
    // single-eval draft. Single evals still need their draft up to date
    // so the playground sees the latest instructions/code/config.
    if (mode === "single" && !draftId) {
      enqueueSnackbar("Draft not ready yet, please wait", {
        variant: "warning",
      });
      return;
    }
    // Arm a fresh epoch for this test. If an older test is still in flight,
    // its late result will compare against this new epoch and be ignored.
    testEpochRef.current += 1;
    activeTestEpochRef.current = testEpochRef.current;
    setIsTesting(true);
    setTestError(null);
    setTestPassed(false);

    try {
      if (mode === "single") {
        await updateDraft.mutateAsync(buildUpdatePayload());
        testPlaygroundRef.current?.runTest?.(draftId);
      } else {
        testPlaygroundRef.current?.runTest?.();
      }
      setTimeout(() => setIsTesting((v) => (v ? false : v)), 60000);
    } catch (error) {
      const message =
        error?.response?.data?.result || error?.message || "Failed to run test";
      handleTestResult(false, message);
      setIsTesting(false);
    }
  }, [
    mode,
    draftId,
    buildUpdatePayload,
    updateDraft,
    handleTestResult,
    enqueueSnackbar,
  ]);

  const isLoading =
    createEval.isLoading || createComposite.isLoading || updateDraft.isLoading;

  // Block mode switch when a test has run or is running. Save-in-flight is
  // excluded: the save button is already disabled while `isLoading`, so the
  // user can't double-fire, and including it here would open a dialog about
  // "clearing test results" when there are none to clear.
  const hasProgressToDiscard =
    testPassed || testError !== null || isTesting;

  const handleModeChange = useCallback(
    (_, val) => {
      if (val === mode) return;
      if (hasProgressToDiscard) {
        setPendingMode(val);
        return;
      }
      setMode(val);
    },
    [mode, hasProgressToDiscard],
  );

  const handleConfirmModeSwitch = useCallback(() => {
    // Invalidate any in-flight test so its late result won't land in the new
    // mode. activeTestEpochRef is intentionally not touched — the mismatch
    // between it and testEpochRef is what makes handleTestResult bail.
    testEpochRef.current += 1;
    if (pendingMode) setMode(pendingMode);
    setPendingMode(null);
    setTestPassed(false);
    setTestError(null);
    setIsTesting(false);
    // TestPlayground is remounted via `key={mode}`, so its internal state
    // (isRunning / result / error) is discarded automatically — no reset() needed.
  }, [pendingMode]);

  const handleCancelModeSwitch = useCallback(() => {
    setPendingMode(null);
  }, []);
  // Mirrors the Test button's content rules: prompt-based single evals must
  // have a template that actually references inputs, otherwise the saved
  // eval can't be run against real data.
  const singleHasInstructionVariables =
    !!instructions.trim() &&
    extractVariables(instructions, templateFormat).length > 0;
  const canSaveSingle =
    !!name.trim() &&
    (evalType === "code" ? !!code.trim() : singleHasInstructionVariables);
  const canSaveComposite = compositeName.trim() && selectedChildren.length > 0;
  // Single evals require a successful test run before save. Composites
  // don't have a test flow in the create page — their children already exist
  // and can be tested individually.
  const canSave = mode === "single" ? canSaveSingle : canSaveComposite;

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
      }}
    >
      {/* Header — breadcrumb only */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          mb: 2,
          flexShrink: 0,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
          <Breadcrumbs>
            <Link
              underline="hover"
              color="text.secondary"
              sx={{ cursor: "pointer" }}
              onClick={() => navigate("/dashboard/evaluations")}
            >
              Evals List
            </Link>
            <Typography color="text.primary" fontWeight={600}>
              Create evaluation
            </Typography>
          </Breadcrumbs>
          {draftId && (
            <Typography
              variant="caption"
              sx={{
                fontFamily: "monospace",
                fontSize: "10px",
                color: "text.disabled",
                backgroundColor: (t) =>
                  t.palette.mode === "dark"
                    ? "rgba(255,255,255,0.05)"
                    : "action.hover",
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

      {/* Two-panel layout — resizable, fills remaining height */}
      <Box sx={{ flex: 1, minHeight: 0 }}>
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
                px: 0.5,
                pr: 2,
                py: 0.5,
                height: "100%",
              }}
            >
              {/* Single / Composite toggle */}
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
                  onChange={handleModeChange}
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
                  <Tab
                    value="single"
                    label="Single"
                    sx={{
                      bgcolor:
                        mode === "single"
                          ? (theme) =>
                              theme.palette.mode === "dark"
                                ? "rgba(255,255,255,0.12)"
                                : "background.paper"
                          : "transparent",
                      boxShadow:
                        mode === "single"
                          ? (theme) =>
                              theme.palette.mode === "dark"
                                ? "none"
                                : "0 1px 3px rgba(0,0,0,0.08)"
                          : "none",
                      borderRadius: "6px",
                      fontWeight: mode === "single" ? 600 : 400,
                      color:
                        mode === "single" ? "text.primary" : "text.disabled",
                    }}
                  />
                  <Tab
                    value="composite"
                    label="Composite"
                    sx={{
                      bgcolor:
                        mode === "composite"
                          ? (theme) =>
                              theme.palette.mode === "dark"
                                ? "rgba(255,255,255,0.12)"
                                : "background.paper"
                          : "transparent",
                      boxShadow:
                        mode === "composite"
                          ? (theme) =>
                              theme.palette.mode === "dark"
                                ? "none"
                                : "0 1px 3px rgba(0,0,0,0.08)"
                          : "none",
                      borderRadius: "6px",
                      fontWeight: mode === "composite" ? 600 : 400,
                      color:
                        mode === "composite" ? "text.primary" : "text.disabled",
                    }}
                  />
                </Tabs>
              </Box>

              {mode === "single" ? (
                /* =================== SINGLE EVAL FORM =================== */
                <>
                  {/* Eval Name */}
                  <Box>
                    <Typography
                      variant="body2"
                      fontWeight={600}
                      sx={{ mb: 0.5 }}
                    >
                      Eval Name<span style={{ color: "red" }}>*</span>
                    </Typography>
                    <TextField
                      fullWidth
                      size="small"
                      placeholder="Eg: Hallucination detector"
                      value={name}
                      onChange={(e) =>
                        setName(
                          e.target.value
                            .toLowerCase()
                            .replace(/[^a-z0-9_-]/g, ""),
                        )
                      }
                      helperText="Enter unique evaluation name"
                    />
                  </Box>

                  {/* Eval Type Toggle — pill tabs (same as EvalAccordion Text/Image/Audio) */}
                  <Tabs
                    value={evalType}
                    onChange={(_, val) => setEvalType(val)}
                    variant="standard"
                    scrollButtons={false}
                    TabIndicatorProps={{ style: { display: "none" } }}
                    sx={{
                      minHeight: 28,
                      "& .MuiTabs-scroller": { overflow: "visible !important" },
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

                  {/* ═══ Tab-specific content ═══ */}

                  {/* Agents tab — instruction editor with model bar inside */}
                  {evalType === "agent" && (
                    <InstructionEditor
                      value={instructions}
                      onChange={setInstructions}
                      model={model}
                      onModelChange={setModel}
                      placeholder="You are a helpful assistant"
                      templateFormat={templateFormat}
                      onTemplateFormatChange={setTemplateFormat}
                      datasetColumns={datasetColumns}
                      datasetJsonSchemas={datasetJsonSchemas}
                      mode={agentMode}
                      onModeChange={setAgentMode}
                      useInternet={checkInternet}
                      onUseInternetChange={setCheckInternet}
                      activeSummary={summaryType}
                      onActiveSummaryChange={setSummaryType}
                      activeConnectorIds={connectorIds}
                      onActiveConnectorIdsChange={setConnectorIds}
                      selectedKBs={knowledgeBaseIds}
                      onSelectedKBsChange={setKnowledgeBaseIds}
                      activeContextOptions={contextOptions}
                      onActiveContextOptionsChange={setContextOptions}
                    />
                  )}

                  {/* LLM-As-A-Judge tab — message editor (with model +
                      template format in its top bar) and few-shot. */}
                  {evalType === "llm" && (
                    <>
                      {/* Message editor with Falcon AI. Model + template
                          format render inline in LLMPromptEditor's top
                          bar, matching the agent InstructionEditor. */}
                      <LLMPromptEditor
                        messages={messages}
                        onMessagesChange={(msgs) => {
                          setMessages(msgs);
                          const sysMsg = msgs.find((m) => m.role === "system");
                          if (sysMsg) setInstructions(sysMsg.content);
                        }}
                        templateFormat={templateFormat}
                        onTemplateFormatChange={setTemplateFormat}
                        model={model}
                        onModelChange={setModel}
                        datasetColumns={datasetColumns}
                        datasetJsonSchemas={datasetJsonSchemas}
                      />

                      {/* Few-shot examples — dataset selector */}
                      <FewShotExamples
                        selectedDatasets={fewShotExamples}
                        onChange={setFewShotExamples}
                      />
                    </>
                  )}

                  {/* Code tab — Monaco editor with Falcon AI */}
                  {evalType === "code" && (
                    <CodeEvalEditor
                      code={code}
                      setCode={setCode}
                      codeLanguage={codeLanguage}
                      setCodeLanguage={setCodeLanguage}
                      datasetColumns={datasetColumns}
                    />
                  )}

                  {/* Output Type — Code evals only have scoring (0-1) with pass threshold */}
                  {evalType === "code" ? (
                    <Box>
                      <Typography
                        variant="body2"
                        fontWeight={600}
                        sx={{ mb: 0.5 }}
                      >
                        Scoring
                      </Typography>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ mb: 1.5, display: "block" }}
                      >
                        Code evaluator returns a score between 0 and 1. Set a
                        pass threshold below.
                      </Typography>
                      <Typography
                        variant="body2"
                        fontWeight={600}
                        sx={{ mb: 0.5 }}
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
                          value={passThreshold * 100}
                          onChange={(_, val) => setPassThreshold(val / 100)}
                          min={0}
                          max={100}
                          size="small"
                          valueLabelDisplay="auto"
                          valueLabelFormat={(v) => `${Math.round(v)}%`}
                        />
                        <Typography variant="caption">100%</Typography>
                      </Box>
                    </Box>
                  ) : (
                    <OutputTypeConfig
                      outputType={outputType}
                      onOutputTypeChange={setOutputType}
                      choiceScores={choiceScores}
                      onChoiceScoresChange={setChoiceScores}
                      passThreshold={passThreshold}
                      onPassThresholdChange={setPassThreshold}
                      multiChoice={multiChoice}
                      onMultiChoiceChange={setMultiChoice}
                    />
                  )}

                  {/* Error Localization — LLM/Agent only. Code evals don't
                      produce model traces for the localizer to introspect. */}
                  {evalType !== "code" && (
                    <Box>
                      <FormControlLabel
                        control={
                          <Checkbox
                            checked={errorLocalizerEnabled}
                            onChange={(e) =>
                              setErrorLocalizerEnabled(e.target.checked)
                            }
                            size="small"
                          />
                        }
                        label={
                          <Typography variant="body2" fontWeight={500}>
                            Error Localization
                          </Typography>
                        }
                        sx={{ ml: 0 }}
                      />
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ display: "block", ml: 3.5, mt: -0.5 }}
                      >
                        Pinpoints which parts of the input caused evaluation
                        failures
                      </Typography>
                    </Box>
                  )}

                  {/* Advanced — collapsible */}
                  <Box>
                    <Button
                      size="small"
                      onClick={() => setShowAdvanced((p) => !p)}
                      startIcon={
                        <Iconify
                          icon={
                            showAdvanced ? "mdi:chevron-up" : "mdi:chevron-down"
                          }
                          width={16}
                        />
                      }
                      sx={{
                        textTransform: "none",
                        fontSize: "13px",
                        color: "text.secondary",
                        fontWeight: 500,
                        px: 0,
                        "&:hover": {
                          bgcolor: "transparent",
                          color: "text.primary",
                        },
                      }}
                    >
                      Advanced
                      {(description || tags.length > 0) && (
                        <Chip
                          label={[
                            description && "description",
                            tags.length > 0 && `${tags.length} tags`,
                          ]
                            .filter(Boolean)
                            .join(", ")}
                          size="small"
                          sx={{ ml: 1, fontSize: "10px", height: 18 }}
                        />
                      )}
                    </Button>

                    {showAdvanced && (
                      <Box
                        sx={{
                          display: "flex",
                          flexDirection: "column",
                          gap: 2,
                          mt: 1.5,
                          pl: 0.5,
                        }}
                      >
                        {/* Description */}
                        <Box>
                          <Typography
                            variant="caption"
                            fontWeight={600}
                            color="text.secondary"
                            sx={{ mb: 0.5, display: "block" }}
                          >
                            Description
                          </Typography>
                          <TextField
                            fullWidth
                            size="small"
                            multiline
                            minRows={2}
                            placeholder="What does this evaluation check?"
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                          />
                        </Box>

                        {/* Tags */}
                        <Box>
                          <Typography
                            variant="caption"
                            fontWeight={600}
                            color="text.secondary"
                            sx={{ mb: 0.75, display: "block" }}
                          >
                            Tags
                          </Typography>
                          <Box
                            sx={{
                              display: "flex",
                              flexWrap: "wrap",
                              gap: 0.75,
                            }}
                          >
                            {EVAL_TAGS.map((tag) => {
                              const selected = tags.includes(tag.value);
                              return (
                                <Chip
                                  key={tag.value}
                                  icon={<Iconify icon={tag.icon} width={14} />}
                                  label={tag.label}
                                  size="small"
                                  variant={selected ? "filled" : "outlined"}
                                  color={selected ? "primary" : "default"}
                                  onClick={() =>
                                    setTags((prev) =>
                                      selected
                                        ? prev.filter((t) => t !== tag.value)
                                        : [...prev, tag.value],
                                    )
                                  }
                                  sx={{
                                    fontSize: "12px",
                                    cursor: "pointer",
                                    "& .MuiChip-icon": { fontSize: "14px" },
                                  }}
                                />
                              );
                            })}
                          </Box>
                        </Box>
                      </Box>
                    )}
                  </Box>
                </>
              ) : (
                /* =================== COMPOSITE EVAL FORM =================== */
                <CompositeDetailPanel
                  editable
                  name={compositeName}
                  description={compositeDescription}
                  aggregationEnabled={aggregationEnabled}
                  aggregationFunction={aggregationFunction}
                  compositeChildAxis={compositeChildAxis}
                  childWeights={childWeights}
                  children={selectedChildren}
                  onNameChange={setCompositeName}
                  onDescriptionChange={setCompositeDescription}
                  onAggregationEnabledChange={setAggregationEnabled}
                  onAggregationFunctionChange={setAggregationFunction}
                  onCompositeChildAxisChange={setCompositeChildAxis}
                  onChildrenChange={setSelectedChildren}
                  onChildWeightsChange={setChildWeights}
                />
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
              {/* Composite mode reuses the same TestPlayground as single
                  evals — the playground's own tabs let users test against
                  a dataset / tracing / simulation source. The required
                  keys are the deduplicated union of all child variables,
                  which is what the composite itself will need mapped at
                  bind time. */}
              <Box sx={{ flex: 1, overflow: "auto", minHeight: 0 }}>
                <TestPlayground
                  key={mode}
                  ref={testPlaygroundRef}
                  templateId={draftId}
                  model={model}
                  instructions={
                    mode === "composite" || evalType === "code"
                      ? ""
                      : evalType === "llm"
                        ? messages.map((m) => m.content || "").join("\n")
                        : instructions
                  }
                  evalType={mode === "composite" ? "llm" : evalType}
                  code={code}
                  codeLanguage={codeLanguage}
                  requiredKeys={
                    mode === "composite" ? compositeUnionKeys : undefined
                  }
                  isComposite={mode === "composite"}
                  compositeAdhocConfig={
                    mode === "composite"
                      ? {
                          child_template_ids: selectedChildren.map(
                            (c) => c.child_id,
                          ),
                          aggregation_enabled: aggregationEnabled,
                          aggregation_function: aggregationFunction,
                          composite_child_axis: compositeChildAxis || "",
                          child_weights:
                            Object.keys(childWeights || {}).length > 0
                              ? childWeights
                              : null,
                          pass_threshold: passThreshold ?? 0.5,
                        }
                      : null
                  }
                  showVersions={false}
                  onTestResult={handleTestResult}
                  onColumnsLoaded={handleColumnsLoaded}
                  errorLocalizerEnabled={
                    mode === "composite" ? false : errorLocalizerEnabled
                  }
                  templateFormat={templateFormat}
                />
              </Box>

              {/* Action buttons — pinned to bottom */}
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "flex-end",
                  alignItems: "center",
                  gap: 1,
                  pt: 1.5,
                  mt: "auto",
                  borderTop: "1px solid",
                  borderColor: "divider",
                  flexShrink: 0,
                  pb: 0.5,
                }}
              >
                {/* Test status indicator */}
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
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 0.5,
                      mr: "auto",
                    }}
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

                {(() => {
                  const hasCompositeChildren = selectedChildren.length > 0;
                  const hasCode = !!code.trim();
                  const hasInstructions = !!instructions.trim();
                  const instructionVariables = hasInstructions
                    ? extractVariables(instructions, templateFormat)
                    : [];
                  const hasInstructionVariables =
                    instructionVariables.length > 0;
                  const instructionsReady =
                    hasInstructions && hasInstructionVariables;
                  const hasTestInput =
                    mode === "composite"
                      ? hasCompositeChildren
                      : evalType === "code"
                        ? hasCode
                        : instructionsReady;
                  const testDisabled = isTesting || !hasTestInput;

                  let testDisabledReason = "";
                  if (isTesting) {
                    testDisabledReason = "Test is already running.";
                  } else if (mode === "composite" && !hasCompositeChildren) {
                    testDisabledReason =
                      "Add at least one child evaluation to run a test.";
                  } else if (mode !== "composite" && evalType === "code" && !hasCode) {
                    testDisabledReason = "Write some code before running a test.";
                  } else if (
                    mode !== "composite" &&
                    evalType !== "code" &&
                    !hasInstructions
                  ) {
                    testDisabledReason =
                      "Add instructions before running a test.";
                  } else if (
                    mode !== "composite" &&
                    evalType !== "code" &&
                    !hasInstructionVariables
                  ) {
                    testDisabledReason =
                      templateFormat === "jinja"
                        ? 'Your Jinja template has no variables. Reference an input with a {{ variable }} expression or a {% ... %} block (e.g. {{ input }}) so test input can be passed in.'
                        : 'Your Mustache template has no variables. Add a {{variable}} placeholder (e.g. {{input}}) so test input can be passed in.';
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
                          size="small"
                          onClick={handleTestEvaluation}
                          disabled={testDisabled}
                          startIcon={
                            isTesting ? (
                              <CircularProgress size={14} />
                            ) : (
                              <Iconify
                                icon="mdi:play-circle-outline"
                                width={16}
                              />
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
                  const saveDisabled = isLoading || !canSave;
                  let saveDisabledReason = "";
                  if (isLoading) {
                    saveDisabledReason = "Save is already in progress.";
                  } else if (mode === "composite") {
                    if (!compositeName.trim()) {
                      saveDisabledReason =
                        "Give this composite a name before saving.";
                    } else if (selectedChildren.length === 0) {
                      saveDisabledReason =
                        "Add at least one child evaluation before saving.";
                    }
                  } else if (!name.trim()) {
                    saveDisabledReason = "Give this evaluation a name before saving.";
                  } else if (evalType === "code" && !code.trim()) {
                    saveDisabledReason = "Write some code before saving.";
                  } else if (evalType !== "code" && !instructions.trim()) {
                    saveDisabledReason = "Add instructions before saving.";
                  } else if (
                    evalType !== "code" &&
                    !singleHasInstructionVariables
                  ) {
                    saveDisabledReason =
                      templateFormat === "jinja"
                        ? "Your Jinja template has no variables. Reference an input with a {{ variable }} expression or a {% ... %} block (e.g. {{ input }}) before saving."
                        : "Your Mustache template has no variables. Add a {{variable}} placeholder (e.g. {{input}}) before saving.";
                  }

                  return (
                    <CustomTooltip
                      show={saveDisabled && !!saveDisabledReason}
                      type=""
                      title={saveDisabledReason}
                      arrow
                    >
                      <span>
                        <Button
                          variant="contained"
                          size="small"
                          onClick={
                            mode === "single"
                              ? handleSaveSingle
                              : handleSaveComposite
                          }
                          disabled={saveDisabled}
                          startIcon={
                            <Iconify
                              icon="mdi:content-save-outline"
                              width={16}
                            />
                          }
                          sx={{ textTransform: "none" }}
                        >
                          {isLoading ? "Saving..." : "Save Evaluation"}
                        </Button>
                      </span>
                    </CustomTooltip>
                  );
                })()}
              </Box>
            </Box>
          }
        />
      </Box>
      <UnsavedChangesDialog
        open={pendingMode !== null}
        onClose={handleCancelModeSwitch}
        onConfirm={handleConfirmModeSwitch}
        title="Discard test results?"
        message={`Switching to ${pendingMode === "single" ? "Single" : "Composite"} will clear your current test results. Continue?`}
        confirmLabel="Confirm"
      />
    </Box>
  );
};

export default EvalCreatePage;

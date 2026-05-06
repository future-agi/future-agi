import {
  Box,
  Breadcrumbs,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  FormControlLabel,
  IconButton,
  Link,
  Menu,
  MenuItem,
  Slider,
  Tab,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate, useParams } from "react-router";
import { useSearchParams } from "react-router-dom";
import { useSnackbar } from "notistack";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";

import { useEvalDetail, useUpdateEval } from "../hooks/useEvalDetail";
import {
  useCreateEvalVersion,
  useEvalVersions,
} from "../hooks/useEvalVersions";
import {
  useCompositeDetail,
  useUpdateCompositeEval,
} from "../hooks/useCompositeEval";
import InstructionEditor from "./InstructionEditor";
import { extractJinjaVariables } from "src/utils/jinjaVariables";
import LLMPromptEditor from "./LLMPromptEditor";
import OutputTypeConfig from "./OutputTypeConfig";
import FewShotExamples from "./FewShotExamples";
import CodeEvalEditor from "./CodeEvalEditor";
import ResizablePanels from "src/components/resizablePanels/ResizablePanels";
import TestPlayground from "./TestPlayground";
import CompositeDetailPanel from "./CompositeDetailPanel";
import EvalFeedbackTab from "./EvalFeedbackTab";
import EvalGroundTruthTab from "./EvalGroundTruthTab";
import EvalUsageTab from "./EvalUsageTab";
import VersionBadge from "./VersionBadge";
import { EVAL_TAGS } from "../constant";
import { FAGI_MODEL_VALUES } from "./ModelSelector";

const extract_selected_tools = (tools) => {
  if (Array.isArray(tools)) return tools;
  if (tools && typeof tools === "object") {
    return Object.entries(tools)
      .filter(([, enabled]) => !!enabled)
      .map(([name]) => name);
  }
  return [];
};

const build_tools_payload = (selected_tools) =>
  (selected_tools || []).reduce((acc, tool_name) => {
    if (tool_name) acc[tool_name] = true;
    return acc;
  }, {});

const resolve_summary_type = (summary) => {
  if (summary && typeof summary === "object" && summary.type) {
    return summary.type;
  }
  if (typeof summary === "string" && summary.trim()) return summary;
  return "concise";
};

const resolve_context_options = (data_injection) => {
  if (!data_injection || typeof data_injection !== "object") {
    return ["variables_only"];
  }
  const opts = [];
  if (data_injection.full_row || data_injection.fullRow) opts.push("dataset_row");
  if (data_injection.span_context || data_injection.spanContext) opts.push("span_context");
  if (data_injection.trace_context || data_injection.traceContext) opts.push("trace_context");
  if (data_injection.session_context || data_injection.sessionContext) opts.push("session_context");
  if (data_injection.call_context || data_injection.callContext) opts.push("call_context");
  if (opts.length > 0) return opts;
  if (data_injection.variables_only === false || data_injection.variablesOnly === false) {
    return ["full_row"];
  }
  return ["variables_only"];
};

const getEvalPromptText = (evalData, config = {}) =>
  evalData?.instructions || config?.rule_prompt || "";

const EvalDetailPage = () => {
  const { evalId } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { enqueueSnackbar } = useSnackbar();
  const { isOSS } = useDeploymentMode();

  const {
    data: evalData,
    isLoading,
    error: fetchError,
  } = useEvalDetail(evalId);
  const updateEval = useUpdateEval(evalId);
  const createVersion = useCreateEvalVersion(evalId);
  const { data: versionsData } = useEvalVersions(evalId);
  const testPlaygroundRef = useRef(null);

  // Editable fields
  const [instructions, setInstructions] = useState("");
  const [code, setCode] = useState("");
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
  const [connectorIds, setConnectorIds] = useState([]);
  const [knowledgeBaseIds, setKnowledgeBaseIds] = useState([]);
  const [contextOptions, setContextOptions] = useState(["variables_only"]);
  const [tags, setTags] = useState([]);
  const [messages, setMessages] = useState([{ role: "system", content: "" }]);
  const [fewShotExamples, setFewShotExamples] = useState([]);
  const [templateFormat, setTemplateFormat] = useState(
    () =>
      evalData?.template_format ||
      (evalData?.config || {}).template_format ||
      "mustache",
  );
  const [errorLocalizerEnabled, setErrorLocalizerEnabled] = useState(false);

  // Dataset columns for autocomplete
  const [datasetColumns, setDatasetColumns] = useState([]);
  const [datasetJsonSchemas, setDatasetJsonSchemas] = useState({});
  const handleColumnsLoaded = useCallback((cols, jsonSchemas) => {
    setDatasetColumns(cols || []);
    setDatasetJsonSchemas(jsonSchemas || {});
  }, []);

  // Test state
  const [testPassed, setTestPassed] = useState(false);
  const [testError, setTestError] = useState(null);
  const [isTesting, setIsTesting] = useState(false);
  const [isPlaygroundReady, setIsPlaygroundReady] = useState(false);

  // Auto-dismiss test error after 6 seconds
  useEffect(() => {
    if (!testError) return;
    const timer = setTimeout(() => setTestError(null), 6000);
    return () => clearTimeout(timer);
  }, [testError]);

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

  // Top-level tab — sync with URL ?tab= param
  const [activeTab, setActiveTab] = useState(
    () => searchParams.get("tab") || "details",
  );

  const handleTabChange = useCallback(
    (_, val) => {
      setActiveTab(val);
      setTestError(null);
      setTestPassed(false);
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (val === "details") next.delete("tab");
          else next.set("tab", val);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // Track dirty state
  const [isDirty, setIsDirty] = useState(false);
  // When true, suppress auto-dirty marking because we're programmatically
  // populating form state from the fetched eval. Controlled editors like
  // Quill can emit spurious onChange events when their value prop changes,
  // which would otherwise flip `isDirty` to true on every fresh load.
  const isPopulatingRef = useRef(false);
  const markDirty = useCallback(() => {
    if (!isPopulatingRef.current) {
      setIsDirty(true);
      setTestError(null);
      setTestPassed(false);
    }
  }, []);

  // Derived flags — must be above handleVersionSelect which uses isComposite
  const isComposite = evalData?.template_type === "composite";

  // Version viewing state
  const [viewingVersion, setViewingVersion] = useState(null);
  const defaultVersion = useMemo(() => {
    const list = versionsData?.versions || [];
    if (!list.length) return null;
    const byFlag = list.find((v) => v.is_default || v.isDefault);
    if (byFlag) return byFlag;
    return [...list].sort(
      (a, b) =>
        (a.version_number ?? a.versionNumber ?? Number.MAX_SAFE_INTEGER) -
        (b.version_number ?? b.versionNumber ?? Number.MAX_SAFE_INTEGER),
    )[0];
  }, [versionsData]);

  const handleVersionSelect = useCallback(
    (version) => {
      const versionToLoad = version || defaultVersion;
      // Warn if switching versions with unsaved changes
      if (isDirty && version !== null) {
        if (
          !window.confirm(
            "You have unsaved changes. Discard and switch version?",
          )
        )
          return;
      }

      if (!versionToLoad) {
        // Deselect — restore to current eval data
        isPopulatingRef.current = true;
        setViewingVersion(null);
        setSearchParams(
          (prev) => {
            prev.delete("v");
            return prev;
          },
          { replace: true },
        );
        if (evalData) {
          const config = evalData.config || {};
          const promptText = getEvalPromptText(evalData, config);
          setTemplateFormat(
            evalData.template_format || config.template_format || "mustache",
          );
          // Type-aware split (see initial-load effect below for the rationale).
          const _type = evalData.eval_type || "llm";
          if (_type === "code") {
            setInstructions("");
            setCode(config.code || evalData.code || "");
          } else {
            setInstructions(promptText);
            setCode("");
          }
          setCodeLanguage(config.language || "python");
          setModel(config.model || evalData.model || ("turing_large"));
          setOutputType(
            evalData.output_type ||
              evalData.output_type_normalized ||
              "pass_fail",
          );
          setPassThreshold(evalData.pass_threshold ?? 0.5);
          // Derive choiceScores: use the backend's scored dict if present,
          // otherwise convert the `choices` array (used by deterministic /
          // multi-class evals like tone, customer_agent_*) into a default
          // scored map so the UI renders the choices.
          {
            const scored = evalData.choice_scores;
            if (scored && Object.keys(scored).length > 0) {
              setChoiceScores(scored);
            } else if (
              Array.isArray(evalData.choices) &&
              evalData.choices.length > 0
            ) {
              const derived = {};
              for (const label of evalData.choices) {
                derived[label] = 0.5;
              }
              setChoiceScores(derived);
            } else {
              setChoiceScores({});
            }
          }
          setMultiChoice(
            evalData.multi_choice ??
              config.multi_choice ??
              config.multi_choice ??
              false,
          );
          setDescription(evalData.description || "");
          setCheckInternet(config.check_internet ?? false);
          setAgentMode(config.agent_mode || "agent");
          setSummaryType(resolve_summary_type(config.summary));
          setConnectorIds(extract_selected_tools(config.tools));
          setKnowledgeBaseIds(
            Array.isArray(config.knowledge_bases) ? config.knowledge_bases : [],
          );
          setContextOptions(resolve_context_options(config.data_injection));
          setErrorLocalizerEnabled(
            evalData.error_localizer_enabled ??
              config.error_localizer_enabled ??
              false,
          );
          setTags(evalData.tags || evalData.eval_tags || []);
          if (config.messages && config.messages.length > 0) {
            setMessages(config.messages);
          } else if (evalData.eval_type === "llm" && promptText) {
            setMessages([{ role: "system", content: promptText }]);
          }
          if (config.few_shot_examples || config.fewShotExamples) {
            setFewShotExamples(
              config.few_shot_examples || config.fewShotExamples || [],
            );
          }
        }
        setIsDirty(false);
        setTimeout(() => {
          isPopulatingRef.current = false;
        }, 0);
        return;
      }

      // Load version config into the form
      isPopulatingRef.current = true;
      setViewingVersion(versionToLoad);
      setSearchParams(
        { v: versionToLoad.version_number ?? versionToLoad.versionNumber },
        { replace: true },
      );
      const config =
        versionToLoad.config_snapshot || versionToLoad.configSnapshot || {};
      const promptText = versionToLoad.criteria || config.rule_prompt || "";

      // Composite versions: load aggregation + children from snapshot
      if (isComposite) {
        if (config.aggregation_enabled != null) {
          setCompositeAggEnabled(config.aggregation_enabled);
        }
        if (config.aggregation_function) {
          setCompositeAggFunction(config.aggregation_function);
        }
        if (config.composite_child_axis != null) {
          setCompositeChildAxis(config.composite_child_axis);
        }
        if (config.children) {
          setCompositeChildren(
            config.children.map((c) => ({
              child_id: c.child_id,
              child_name: c.child_name,
              order: c.order,
              weight: c.weight ?? 1.0,
            })),
          );
          const weights = {};
          config.children.forEach((c) => {
            weights[c.child_id] = c.weight ?? 1.0;
          });
          setCompositeChildWeights(weights);
        }
        if (versionToLoad.criteria != null) {
          setCompositeDescription(versionToLoad.criteria);
        }
        setIsDirty(false);
        setTimeout(() => {
          isPopulatingRef.current = false;
        }, 0);
        return;
      }

      // Type-aware split. A version snapshot's `criteria` field contains
      // the code text for code evals (since template.criteria is the
      // authoritative store for code), and the prompt for agent/llm.
      const _type = evalData?.eval_type || "llm";
      if (_type === "code") {
        setInstructions("");
        setCode(config.code || "");
      } else {
        setInstructions(promptText);
        setCode("");
      }
      setCodeLanguage(config.language || "python");
      setModel(config.model || versionToLoad.model || ("turing_large"));
      {
        const outputMap = {
          "Pass/Fail": "pass_fail",
          score: "percentage",
          numeric: "percentage",
          reason: "percentage",
          choices: "deterministic",
          "": "percentage",
        };
        const fallbackOutput =
          evalData?.output_type ||
          evalData?.output_type_normalized ||
          "pass_fail";
        setOutputType(
          config.output
            ? outputMap[config.output] || "percentage"
            : fallbackOutput,
        );
      }
      setPassThreshold(config.passThreshold ?? config.pass_threshold ?? 0.5);
      setChoiceScores(config.choiceScores || config.choice_scores || {});
      setMultiChoice(config.multiChoice ?? config.multi_choice ?? false);
      if (config.messages && config.messages.length > 0) {
        setMessages(config.messages);
      } else if (evalData.eval_type === "llm" && promptText) {
        setMessages([{ role: "system", content: promptText }]);
      }
      if (config.few_shot_examples || config.fewShotExamples) {
        setFewShotExamples(
          config.few_shot_examples || config.fewShotExamples || [],
        );
      }
      setCheckInternet(config.check_internet ?? false);
      setAgentMode(config.agent_mode || "agent");
      setSummaryType(resolve_summary_type(config.summary));
      setConnectorIds(extract_selected_tools(config.tools));
      setKnowledgeBaseIds(
        Array.isArray(config.knowledge_bases) ? config.knowledge_bases : [],
      );
      setContextOptions(resolve_context_options(config.data_injection));
      setErrorLocalizerEnabled(config.error_localizer_enabled ?? false);
      setIsDirty(false);
      setTimeout(() => {
        isPopulatingRef.current = false;
      }, 0);
    },
    [defaultVersion, evalData, isDirty, isComposite, setSearchParams, isOSS],
  );

  // Three-dot menu
  const [menuAnchor, setMenuAnchor] = useState(null);

  // Warn on page leave with unsaved changes
  useEffect(() => {
    const handler = (e) => {
      if (isDirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  // Populate form from API data — skip when viewing a specific version
  const initialLoadDone = useRef(false);
  useEffect(() => {
    if (evalData && !viewingVersion) {
      // On initial load, always populate. On subsequent refetches, only populate
      // if the user hasn't made edits (isDirty).
      if (!initialLoadDone.current || !isDirty) {
        // Guard against spurious onChange events from controlled editors:
        // set the populating flag BEFORE any setter, so any onChange that
        // fires while React flushes these updates is ignored by markDirty.
        isPopulatingRef.current = true;
        const config = evalData.config || {};
        const promptText = getEvalPromptText(evalData, config);
        // Set template format BEFORE instructions so the InstructionEditor
        // mounts with the correct key (it uses key={templateFormat-...})
        // and doesn't lose content on remount.
        setTemplateFormat(
          evalData.template_format || config.template_format || "mustache",
        );
        // Type-aware split. Backend returns `instructions = template.criteria`
        // for every eval type — for code evals that field contains the
        // description text, not a Jinja prompt. Loading it into the
        // `instructions` state would let the description leak into the
        // variable extractor and the prompt editor (which isn't even shown
        // for code evals). Only load the field that matches the type.
        const _type = evalData.eval_type || "llm";
        if (_type === "code") {
          setInstructions("");
          setCode(config.code || evalData.code || "");
        } else {
          setInstructions(promptText);
          setCode("");
        }
        setCodeLanguage(
          config.language ||
            config.code_language ||
            evalData.code_language ||
            "python",
        );
        setModel(config.model || evalData.model || ("turing_large"));
        setOutputType(
          evalData.output_type ||
            evalData.output_type_normalized ||
            "pass_fail",
        );
        setPassThreshold(evalData.pass_threshold ?? 0.5);
        // Derive choiceScores: use the backend's scored dict if present,
        // otherwise convert the `choices` array (used by deterministic /
        // multi-class evals like tone, customer_agent_*) into a default
        // scored map so the UI renders the choices.
        {
          const scored = evalData.choice_scores;
          if (scored && Object.keys(scored).length > 0) {
            setChoiceScores(scored);
          } else if (
            Array.isArray(evalData.choices) &&
            evalData.choices.length > 0
          ) {
            const derived = {};
            for (const label of evalData.choices) {
              derived[label] = 0.5;
            }
            setChoiceScores(derived);
          } else {
            setChoiceScores({});
          }
        }
        setMultiChoice(
          evalData.multi_choice ??
            config.multi_choice ??
            config.multi_choice ??
            false,
        );
        setDescription(evalData.description || "");
        setCheckInternet(config.check_internet ?? false);
        setAgentMode(config.agent_mode || "agent");
        setSummaryType(resolve_summary_type(config.summary));
        setConnectorIds(extract_selected_tools(config.tools));
        setKnowledgeBaseIds(
          Array.isArray(config.knowledge_bases) ? config.knowledge_bases : [],
        );
        setContextOptions(resolve_context_options(config.data_injection));
        setErrorLocalizerEnabled(
          evalData.error_localizer_enabled ??
            config.error_localizer_enabled ??
            false,
        );
        setTags(evalData.tags || evalData.eval_tags || []);
        if (config.messages && config.messages.length > 0) {
          setMessages(config.messages);
        } else if (evalData.eval_type === "llm" && promptText) {
          setMessages([{ role: "system", content: promptText }]);
        }
        if (config.few_shot_examples || config.fewShotExamples) {
          setFewShotExamples(
            config.few_shot_examples || config.fewShotExamples || [],
          );
        }
        setIsDirty(false);
        initialLoadDone.current = true;
        // Release the populating guard after React flushes this batch so
        // any trailing onChange events from the controlled editors land in
        // the next microtask and get suppressed by markDirty().
        setTimeout(() => {
          isPopulatingRef.current = false;
        }, 100);
      }
    }
  }, [evalData]); // eslint-disable-line react-hooks/exhaustive-deps

  const evalType = evalData?.eval_type || "llm";
  const isSystemEval = evalData?.owner === "system";

  // Fetch composite detail (children, weights) when viewing a composite
  const { data: compositeDetail } = useCompositeDetail(evalId, isComposite);
  const updateComposite = useUpdateCompositeEval(evalId);

  // Composite edit state — populated from compositeDetail / evalData
  const [compositeName, setCompositeName] = useState("");
  const [compositeDescription, setCompositeDescription] = useState("");
  const [compositeAggEnabled, setCompositeAggEnabled] = useState(true);
  const [compositeAggFunction, setCompositeAggFunction] =
    useState("weighted_avg");
  const [compositeChildAxis, setCompositeChildAxis] = useState("");
  const [compositeChildren, setCompositeChildren] = useState([]);
  const [compositeChildWeights, setCompositeChildWeights] = useState({});

  // Sync composite edit state from fetched data. Guard with isPopulatingRef
  // so onChange handlers don't flip dirty during the initial populate.
  useEffect(() => {
    if (!isComposite || !compositeDetail) return;
    isPopulatingRef.current = true;
    setCompositeName(compositeDetail.name || "");
    setCompositeDescription(compositeDetail.description || "");
    setCompositeAggEnabled(compositeDetail.aggregation_enabled !== false);
    setCompositeAggFunction(
      compositeDetail.aggregation_function || "weighted_avg",
    );
    setCompositeChildAxis(compositeDetail.composite_child_axis || "");
    setCompositeChildren(compositeDetail.children || []);
    // Seed weights map from children
    const weights = {};
    (compositeDetail.children || []).forEach((c) => {
      if (c.weight != null) weights[c.child_id] = c.weight;
    });
    setCompositeChildWeights(weights);
    // Release populating flag after React flushes these updates
    setTimeout(() => {
      isPopulatingRef.current = false;
    }, 0);
  }, [isComposite, compositeDetail]);

  const isSaving =
    updateEval.isLoading ||
    createVersion.isLoading ||
    updateComposite.isPending;

  // Extract variables for the test panel
  const variables = useMemo(() => {
    // Composite templates have no declared required_keys of their own —
    // the authoritative list is the union of each child's required_keys
    // (backend surfaces these on `CompositeDetailResponse.children`).
    // Without this path the TestPlayground would show no inputs to map
    // and Run Composite would fire with an empty mapping, causing every
    // child to fail at runtime.
    if (isComposite) {
      const union = new Set();
      (compositeDetail?.children || []).forEach((child) => {
        (child?.required_keys || []).forEach((k) => union.add(k));
      });
      return [...union];
    }
    const requiredKeys =
      evalData?.config?.required_keys || evalData?.required_keys || [];
    // For code evals the instructions field is reused by the backend to
    // carry the criteria/description text (there is no Jinja prompt), so
    // skip the {{var}} regex scan — it would otherwise match things like
    // Python f-strings or dict braces inside code and pollute the mapping
    // UI with fake variables.
    if (evalType === "code") {
      return [...new Set(requiredKeys)];
    }
    let templateVars;
    if (templateFormat === "jinja") {
      templateVars = extractJinjaVariables(instructions || "");
    } else {
      const matches =
        (instructions || "").match(/\{\{\s*([^{}]+?)\s*\}\}/g) || [];
      templateVars = matches.map((m) => m.replace(/\{\{|\}\}/g, "").trim());
    }
    return [...new Set([...requiredKeys, ...templateVars])];
  }, [instructions, evalData, evalType, isComposite, compositeDetail]);

  // Save version
  const handleSaveVersion = useCallback(async () => {
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
      const dataInjection =
        contextOptions.length === 0 ||
        (contextOptions.length === 1 && contextOptions[0] === "variables_only")
          ? { variables_only: true }
          : { full_row: true };
      const summary =
        summaryType === "custom"
          ? { type: "custom", custom: "" }
          : { type: summaryType };
      const tools = build_tools_payload(connectorIds);
      // Update the template first
      const payload = {
        instructions: evalType === "code" ? "" : instructions,
        code: evalType === "code" ? code : undefined,
        code_language: evalType === "code" ? codeLanguage : undefined,
        model,
        output_type: outputType,
        pass_threshold: passThreshold,
        choice_scores:
          Object.keys(choiceScores || {}).length > 0 ? choiceScores : null,
        multi_choice: multiChoice,
        description: description || null,
        tags,
        check_internet: checkInternet,
        mode: evalType === "agent" ? agentMode : undefined,
        tools: evalType === "agent" ? tools : undefined,
        knowledge_bases: evalType === "agent" ? knowledgeBaseIds : undefined,
        data_injection: evalType === "agent" ? dataInjection : undefined,
        summary: evalType === "agent" ? summary : undefined,
        error_localizer_enabled: errorLocalizerEnabled,
        template_format: templateFormat,
        messages: evalType === "llm" ? messages : undefined,
        few_shot_examples:
          evalType === "llm" && fewShotExamples.length > 0
            ? fewShotExamples
            : undefined,
      };
      await updateEval.mutateAsync(payload);

      // Build a config snapshot for the version so it captures the full state
      const configSnapshot = {
        ...(evalData?.config || {}),
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
        check_internet: checkInternet,
        agent_mode: evalType === "agent" ? agentMode : undefined,
        tools: evalType === "agent" ? tools : undefined,
        knowledge_bases: evalType === "agent" ? knowledgeBaseIds : undefined,
        data_injection: evalType === "agent" ? dataInjection : undefined,
        summary: evalType === "agent" ? summary : undefined,
        error_localizer_enabled: errorLocalizerEnabled,
        template_format: templateFormat,
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
        `Version V${newVersion?.version_number ?? newVersion?.versionNumber ?? ""} saved`,
        {
          variant: "success",
        },
      );
      setIsDirty(false);
      // Switch to viewing the newly created version
      if (newVersion?.version_number ?? newVersion?.versionNumber) {
        setViewingVersion({ ...newVersion, config_snapshot: configSnapshot });
        setSearchParams(
          { v: newVersion.version_number ?? newVersion.versionNumber },
          { replace: true },
        );
        // Switch to versions tab and highlight the new version
        testPlaygroundRef.current?.switchToVersion?.(newVersion.id);
      } else {
        setViewingVersion(null);
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
    evalType,
    isOSS,
    evalData,
    instructions,
    code,
    codeLanguage,
    model,
    outputType,
    passThreshold,
    choiceScores,
    multiChoice,
    description,
    tags,
    checkInternet,
    agentMode,
    summaryType,
    connectorIds,
    knowledgeBaseIds,
    contextOptions,
    errorLocalizerEnabled,
    messages,
    fewShotExamples,
    updateEval,
    createVersion,
    enqueueSnackbar,
    setSearchParams,
  ]);

  // Save composite eval changes via PATCH — also creates a new version.
  const handleSaveComposite = useCallback(async () => {
    try {
      // Only send weights for children currently in the list
      const weights = {};
      compositeChildren.forEach((c) => {
        const w = compositeChildWeights[c.child_id];
        if (w != null) weights[c.child_id] = w;
      });
      const payload = {
        name: compositeName?.trim() || undefined,
        description: compositeDescription,
        aggregation_enabled: compositeAggEnabled,
        aggregation_function: compositeAggFunction,
        composite_child_axis: compositeChildAxis || undefined,
        child_template_ids: compositeChildren.map((c) => c.child_id),
        child_weights: Object.keys(weights).length > 0 ? weights : null,
      };
      const result = await updateComposite.mutateAsync(payload);
      const vNum = result?.version_number;
      enqueueSnackbar(
        vNum
          ? `Composite evaluation saved — V${vNum}`
          : "Composite evaluation saved",
        { variant: "success" },
      );
      setIsDirty(false);
    } catch (err) {
      const message =
        err?.response?.data?.result ||
        err?.message ||
        "Failed to save composite";
      enqueueSnackbar(
        typeof message === "string" ? message : JSON.stringify(message),
        { variant: "error" },
      );
    }
  }, [
    compositeName,
    compositeDescription,
    compositeAggEnabled,
    compositeAggFunction,
    compositeChildAxis,
    compositeChildren,
    compositeChildWeights,
    updateComposite,
    enqueueSnackbar,
  ]);

  // Test evaluation — auto-saves current config before running
  const handleTestEvaluation = useCallback(async () => {
    setIsTesting(true);
    setTestError(null);
    setTestPassed(false);
    try {
      // Save current config to the template so the eval runner uses the latest
      // state.
      if (!isSystemEval && !isComposite) {
        const dataInjection =
          contextOptions.length === 0 ||
          (contextOptions.length === 1 &&
            contextOptions[0] === "variables_only")
            ? { variables_only: true }
            : { full_row: true };
        const summary =
          summaryType === "custom"
            ? { type: "custom", custom: "" }
            : { type: summaryType };
        const tools = build_tools_payload(connectorIds);
        await updateEval.mutateAsync({
          instructions: evalType === "code" ? "" : instructions,
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
          template_format: templateFormat,
          messages: evalType === "llm" ? messages : undefined,
          few_shot_examples:
            evalType === "llm" && fewShotExamples.length > 0
              ? fewShotExamples
              : undefined,
        });
      }
      // Composite evals: save current children/weights/aggregation config
      // before testing so the execute endpoint picks up the latest state.
      if (isComposite && !isSystemEval) {
        const weights = {};
        compositeChildren.forEach((c) => {
          const w = compositeChildWeights[c.child_id];
          if (w != null) weights[c.child_id] = w;
        });
        await updateComposite.mutateAsync({
          name: compositeName?.trim() || undefined,
          description: compositeDescription,
          aggregation_enabled: compositeAggEnabled,
          aggregation_function: compositeAggFunction,
          composite_child_axis: compositeChildAxis || undefined,
          child_template_ids: compositeChildren.map((c) => c.child_id),
          child_weights: Object.keys(weights).length > 0 ? weights : null,
        });
      }
      testPlaygroundRef.current?.runTest?.(evalId);
    } catch (error) {
      const message =
        error?.response?.data?.result || error?.message || "Failed to run test";
      handleTestResult(false, message);
      setIsTesting(false);
    }
  }, [
    evalId,
    evalType,
    isSystemEval,
    isComposite,
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
    connectorIds,
    knowledgeBaseIds,
    contextOptions,
    errorLocalizerEnabled,
    messages,
    fewShotExamples,
    updateEval,
    updateComposite,
    handleTestResult,
    compositeName,
    compositeDescription,
    compositeAggEnabled,
    compositeAggFunction,
    compositeChildAxis,
    compositeChildren,
    compositeChildWeights,
  ]);

  // Delete
  const handleDelete = useCallback(async () => {
    try {
      await axios.post(endpoints.develop.eval.deleteEvalsTemplate, {
        eval_template_id: evalId,
      });
      enqueueSnackbar("Evaluation deleted", { variant: "success" });
      navigate("/dashboard/evaluations");
    } catch {
      enqueueSnackbar("Failed to delete evaluation", { variant: "error" });
    }
    setMenuAnchor(null);
  }, [evalId, enqueueSnackbar, navigate]);

  // Duplicate
  const handleDuplicate = useCallback(async () => {
    try {
      const { data } = await axios.post(
        endpoints.develop.eval.duplicateEvalsTemplate,
        { eval_template_id: evalId },
      );
      enqueueSnackbar("Evaluation duplicated", { variant: "success" });
      if (data?.result?.id)
        navigate(`/dashboard/evaluations/${data.result.id}`);
    } catch {
      enqueueSnackbar("Failed to duplicate evaluation", { variant: "error" });
    }
    setMenuAnchor(null);
  }, [evalId, enqueueSnackbar, navigate]);

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
        <CircularProgress />
      </Box>
    );
  }

  if (fetchError || !evalData) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          py: 8,
          gap: 2,
        }}
      >
        <Typography color="error">Failed to load evaluation</Typography>
        <Button
          variant="outlined"
          onClick={() => navigate("/dashboard/evaluations")}
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
        minHeight: 0,
      }}
    >
      {/* Header — breadcrumb + version + menu */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          mb: 1.5,
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
              {evalData.name}
            </Typography>
          </Breadcrumbs>
          <VersionBadge
            version={
              viewingVersion
                ? `V${viewingVersion.version_number ?? viewingVersion.versionNumber}`
                : evalData.current_version || "V1"
            }
          />
          <Box
            sx={{
              px: 1,
              py: 0.25,
              borderRadius: "4px",
              fontSize: "11px",
              fontWeight: 600,
              backgroundColor: (theme) =>
                theme.palette.mode === "dark"
                  ? isComposite
                    ? "rgba(99,102,241,0.16)"
                    : evalType === "code"
                      ? "rgba(255,152,0,0.16)"
                      : evalType === "agent"
                        ? "rgba(46,203,113,0.16)"
                        : "rgba(124,77,255,0.16)"
                  : isComposite
                    ? "rgba(99,102,241,0.08)"
                    : evalType === "code"
                      ? "rgba(255,152,0,0.08)"
                      : evalType === "agent"
                        ? "rgba(46,203,113,0.08)"
                        : "rgba(124,77,255,0.08)",
              color: isComposite
                ? "info.main"
                : evalType === "code"
                  ? "warning.main"
                  : evalType === "agent"
                    ? "success.main"
                    : "primary.main",
            }}
          >
            {(() => {
              if (!isComposite) {
                return evalType === "code"
                  ? "Code"
                  : evalType === "agent"
                    ? "Agent"
                    : "LLM";
              }
              const typeLabels = { code: "Code", agent: "Agent", llm: "LLM" };
              const kids =
                compositeChildren?.length > 0
                  ? compositeChildren
                  : compositeDetail?.children || [];
              const childTypes = [
                ...new Set(
                  kids.map(
                    (c) => typeLabels[c.eval_type] || c.eval_type || "LLM",
                  ),
                ),
              ];
              return childTypes.length > 0
                ? childTypes.join(", ")
                : "Composite";
            })()}
          </Box>
        </Box>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          {isSystemEval && (
            <Typography variant="caption" color="text.disabled">
              Read-only (system eval)
            </Typography>
          )}
          <IconButton
            size="small"
            onClick={(e) => setMenuAnchor(e.currentTarget)}
          >
            <Iconify icon="solar:menu-dots-bold" width={18} />
          </IconButton>
          <Menu
            anchorEl={menuAnchor}
            open={Boolean(menuAnchor)}
            onClose={() => setMenuAnchor(null)}
          >
            <MenuItem onClick={handleDuplicate}>
              <Iconify icon="solar:copy-bold" width={16} sx={{ mr: 1 }} />{" "}
              Duplicate
            </MenuItem>
            {!isSystemEval && (
              <MenuItem onClick={handleDelete} sx={{ color: "error.main" }}>
                <Iconify
                  icon="solar:trash-bin-trash-bold"
                  width={16}
                  sx={{ mr: 1 }}
                />{" "}
                Delete
              </MenuItem>
            )}
          </Menu>
        </Box>
      </Box>

      {/* Top Tabs */}
      <Tabs
        value={activeTab}
        onChange={handleTabChange}
        TabIndicatorProps={{ style: { display: "none" } }}
        sx={{
          minHeight: 32,
          mb: 1.5,
          flexShrink: 0,
          "& .MuiTab-root": {
            minHeight: 32,
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
              : "#f4f4f5",
        }}
      >
        {(evalType === "code" || isComposite
          ? ["details", "usage"]
          : ["details", "usage", "feedback", "ground_truth"]
        ).map((tab) => {
          const labels = {
            details: "Eval Details",
            usage: "Usage",
            feedback: "Feedback",
            ground_truth: "Ground Truth",
          };
          return (
            <Tab
              key={tab}
              value={tab}
              label={labels[tab]}
              sx={{
                bgcolor:
                  activeTab === tab
                    ? (theme) =>
                        theme.palette.mode === "dark"
                          ? "rgba(255,255,255,0.12)"
                          : "#fff"
                    : "transparent",
                boxShadow:
                  activeTab === tab
                    ? (theme) =>
                        theme.palette.mode === "dark"
                          ? "none"
                          : "0 1px 3px rgba(0,0,0,0.08)"
                    : "none",
                borderRadius: "6px",
                fontWeight: activeTab === tab ? 600 : 400,
                color: activeTab === tab ? "text.primary" : "text.disabled",
              }}
            />
          );
        })}
      </Tabs>

      {/* ═══ Eval Details Tab — playground layout ═══ */}
      {activeTab === "details" && (
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
                  // System evals: show not-allowed cursor on all disabled MUI inputs
                  ...(isSystemEval && {
                    "& .Mui-disabled": { cursor: "not-allowed !important" },
                    "& .Mui-disabled input, & .Mui-disabled textarea, & .Mui-disabled .MuiSelect-select":
                      { cursor: "not-allowed !important" },
                    "& .Mui-disabled .MuiSlider-thumb, & .Mui-disabled .MuiSlider-track, & .Mui-disabled .MuiSlider-rail":
                      { cursor: "not-allowed" },
                  }),
                }}
              >
                {/* Version viewing banner — only when viewing a non-default older version */}
                {viewingVersion &&
                  !(viewingVersion.is_default ?? viewingVersion.isDefault) && (
                    <Box
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 1,
                        px: 1.5,
                        py: 1,
                        borderRadius: "8px",
                        border: "1px solid",
                        borderColor: "primary.main",
                        backgroundColor: (theme) =>
                          theme.palette.mode === "dark"
                            ? "rgba(124,77,255,0.08)"
                            : "rgba(124,77,255,0.04)",
                      }}
                    >
                      <Iconify
                        icon="solar:eye-bold"
                        width={16}
                        sx={{ color: "primary.main", flexShrink: 0 }}
                      />
                      <Typography
                        variant="caption"
                        sx={{ flex: 1, fontSize: "12px" }}
                      >
                        Viewing{" "}
                        <strong>
                          V
                          {viewingVersion.version_number ??
                            viewingVersion.versionNumber}
                        </strong>{" "}
                        config. Edit and save to create a new version.
                      </Typography>
                      <Button
                        size="small"
                        variant="text"
                        onClick={() => {
                          if (
                            isDirty &&
                            !window.confirm(
                              "You have unsaved changes. Discard and go back to default?",
                            )
                          )
                            return;
                          handleVersionSelect(null);
                        }}
                        sx={{
                          textTransform: "none",
                          fontSize: "12px",
                          minWidth: 0,
                          px: 1,
                        }}
                      >
                        Back to default
                      </Button>
                    </Box>
                  )}

                {/* ═══ Composite-specific config ═══ */}
                {isComposite && (
                  <CompositeDetailPanel
                    name={compositeName}
                    description={compositeDescription}
                    aggregationEnabled={compositeAggEnabled}
                    aggregationFunction={compositeAggFunction}
                    compositeChildAxis={compositeChildAxis}
                    children={compositeChildren}
                    childWeights={compositeChildWeights}
                    editable={!isSystemEval}
                    disabled={isSaving}
                    onNameChange={(v) => {
                      setCompositeName(v);
                      markDirty();
                    }}
                    onDescriptionChange={(v) => {
                      setCompositeDescription(v);
                      markDirty();
                    }}
                    onAggregationEnabledChange={(v) => {
                      setCompositeAggEnabled(v);
                      markDirty();
                    }}
                    onAggregationFunctionChange={(v) => {
                      setCompositeAggFunction(v);
                      markDirty();
                    }}
                    onCompositeChildAxisChange={(v) => {
                      setCompositeChildAxis(v);
                      markDirty();
                    }}
                    onChildrenChange={(v) => {
                      setCompositeChildren(v);
                      markDirty();
                    }}
                    onChildWeightsChange={(v) => {
                      setCompositeChildWeights(v);
                      markDirty();
                    }}
                  />
                )}

                {/* ═══ Eval-type-specific config ═══ */}

                {/*
                System-eval lock-down policy (per product spec):
                  READ-ONLY: instructions, output type, description, tags
                  EDITABLE:  model, agent_mode (pill), + button runtime settings
                             (internet / connectors / KB / summary / data injection),
                             scoring settings WITHIN the chosen output type
                             (choice labels/scores, pass threshold slider)
                User evals: everything editable except the output_type category
                            switch (can edit settings within the type, not switch type).
              */}
                {/* Agent type — InstructionEditor with model bar */}
                {!isComposite && evalType === "agent" && (
                  <InstructionEditor
                    value={instructions}
                    onChange={(v) => {
                      setInstructions(v);
                      markDirty();
                    }}
                    model={model}
                    onModelChange={(v) => {
                      setModel(v);
                      markDirty();
                    }}
                    templateFormat={templateFormat}
                    onTemplateFormatChange={setTemplateFormat}
                    datasetColumns={datasetColumns}
                    datasetJsonSchemas={datasetJsonSchemas}
                    disabled={isSystemEval}
                    modelSelectorDisabled={false}
                    mode={agentMode}
                    onModeChange={(v) => {
                      setAgentMode(v);
                      markDirty();
                    }}
                    useInternet={checkInternet}
                    onUseInternetChange={(v) => {
                      setCheckInternet(v);
                      markDirty();
                    }}
                    activeSummary={summaryType}
                    onActiveSummaryChange={(v) => {
                      setSummaryType(v);
                      markDirty();
                    }}
                    activeConnectorIds={connectorIds}
                    onActiveConnectorIdsChange={(v) => {
                      setConnectorIds(v);
                      markDirty();
                    }}
                    selectedKBs={knowledgeBaseIds}
                    onSelectedKBsChange={(v) => {
                      setKnowledgeBaseIds(v);
                      markDirty();
                    }}
                    activeContextOptions={contextOptions}
                    onActiveContextOptionsChange={(v) => {
                      setContextOptions(v);
                      markDirty();
                    }}
                  />
                )}

                {/* LLM type — message editor (model + template format in
                    its top bar) and few-shot. */}
                {!isComposite && evalType === "llm" && (
                  <>
                    <LLMPromptEditor
                      messages={messages}
                      onMessagesChange={(msgs) => {
                        setMessages(msgs);
                        const sysMsg = msgs.find((m) => m.role === "system");
                        if (sysMsg) setInstructions(sysMsg.content);
                        markDirty();
                      }}
                      templateFormat={templateFormat}
                      onTemplateFormatChange={setTemplateFormat}
                      model={model}
                      onModelChange={(v) => {
                        setModel(v);
                        markDirty();
                      }}
                      datasetColumns={datasetColumns}
                      datasetJsonSchemas={datasetJsonSchemas}
                      disabled={isSystemEval}
                    />
                    <FewShotExamples
                      selectedDatasets={fewShotExamples}
                      onChange={(v) => {
                        setFewShotExamples(v);
                        markDirty();
                      }}
                      disabled={isSystemEval}
                    />
                  </>
                )}

                {/* Code type — Monaco editor with Falcon AI */}
                {!isComposite && evalType === "code" && (
                  <CodeEvalEditor
                    code={code}
                    setCode={(v) => {
                      setCode(v);
                      markDirty();
                    }}
                    codeLanguage={codeLanguage}
                    setCodeLanguage={(v) => {
                      setCodeLanguage(v);
                      markDirty();
                    }}
                    datasetColumns={datasetColumns}
                    disabled={isSystemEval}
                  />
                )}

                {/* Output Type — category locked for both system and user;
                  scoring settings (labels/scores/threshold) editable for both. */}
                {!isComposite &&
                  (evalType === "code" ? (
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
                        sx={{ mb: 1, display: "block" }}
                      >
                        Code evaluator returns a score between 0 and 1.
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
                          onChange={(_, val) => {
                            setPassThreshold(val / 100);
                            markDirty();
                          }}
                          min={0}
                          max={100}
                          size="small"
                          valueLabelDisplay="auto"
                          valueLabelFormat={(v) => `${Math.round(v)}%`}
                          disabled={isSystemEval}
                        />
                        <Typography variant="caption">100%</Typography>
                      </Box>
                    </Box>
                  ) : (
                    <OutputTypeConfig
                      outputType={outputType}
                      onOutputTypeChange={(v) => {
                        setOutputType(v);
                        markDirty();
                      }}
                      choiceScores={choiceScores}
                      onChoiceScoresChange={(v) => {
                        setChoiceScores(v);
                        markDirty();
                      }}
                      passThreshold={passThreshold}
                      onPassThresholdChange={(v) => {
                        setPassThreshold(v);
                        markDirty();
                      }}
                      multiChoice={multiChoice}
                      onMultiChoiceChange={(v) => {
                        setMultiChoice(v);
                        markDirty();
                      }}
                      disabled={isSystemEval}
                      categoryLocked={true}
                    />
                  ))}

                {/* Error Localization */}
                {!isComposite && (
                  <Box>
                    <FormControlLabel
                      control={
                        <Checkbox
                          checked={errorLocalizerEnabled}
                          onChange={(e) => {
                            setErrorLocalizerEnabled(e.target.checked);
                            markDirty();
                          }}
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

                {/* Description */}
                {!isComposite && (
                  <Box>
                    <Typography
                      variant="body2"
                      fontWeight={600}
                      sx={{ mb: 0.5 }}
                    >
                      Description
                    </Typography>
                    <TextField
                      fullWidth
                      size="small"
                      multiline
                      minRows={2}
                      placeholder="Enter a description here"
                      value={description}
                      onChange={(e) => {
                        setDescription(e.target.value);
                        markDirty();
                      }}
                      disabled={isSystemEval}
                    />
                  </Box>
                )}

                {/* Tags */}
                {!isComposite && (
                  <Box>
                    <Typography
                      variant="body2"
                      fontWeight={600}
                      sx={{ mb: 0.5 }}
                    >
                      Tags
                    </Typography>
                    <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75 }}>
                      {/* Selected tags (from backend) + unselected curated tags.
                      All chips share the same UI (icon + outlined/filled).
                      Selected = filled + primary. Unselected = outlined + default. */}
                      {(() => {
                        const curatedValues = new Set(
                          EVAL_TAGS.map((t) => t.value),
                        );

                        // Icon mapping for backend semantic tags. Unknown tags
                        // get a generic bookmark/tag icon.
                        const BACKEND_TAG_ICONS = {
                          SAFETY: "mdi:shield-alert-outline",
                          RAG: "mdi:database-search-outline",
                          HALLUCINATION: "mdi:alert-octagon-outline",
                          CONVERSATION: "mdi:message-text-outline",
                          CHAT: "mdi:robot-outline",
                          AUDIO: "mdi:volume-high",
                          TEXT: "mdi:format-text",
                          IMAGE: "mdi:image-outline",
                          VIDEO: "mdi:video-outline",
                          PDF: "mdi:file-pdf-box",
                          FUNCTION: "mdi:function-variant",
                          LLMS: "mdi:brain",
                          FUTURE_EVALS: "mdi:star-four-points-outline",
                          CUSTOM: "mdi:pencil-outline",
                        };
                        const humanize = (s) =>
                          String(s)
                            .toLowerCase()
                            .replace(/_/g, " ")
                            .replace(/\b\w/g, (m) => m.toUpperCase());

                        // Extra (backend-only) tags not in the curated list
                        const extraTags = tags.filter(
                          (t) => !curatedValues.has(t),
                        );

                        const renderChip = ({
                          key,
                          icon,
                          label,
                          selected,
                          onClick,
                          onDelete,
                        }) => (
                          <Chip
                            key={key}
                            icon={<Iconify icon={icon} width={14} />}
                            label={label}
                            size="small"
                            variant={selected ? "filled" : "outlined"}
                            color={selected ? "primary" : "default"}
                            onClick={isSystemEval ? undefined : onClick}
                            onDelete={
                              isSystemEval || !onDelete ? undefined : onDelete
                            }
                            sx={{
                              fontSize: "12px",
                              cursor: isSystemEval ? "default" : "pointer",
                              "& .MuiChip-icon": { fontSize: "14px" },
                            }}
                          />
                        );

                        return (
                          <>
                            {/* Selected backend-only tags first (always "selected") */}
                            {extraTags.map((t) =>
                              renderChip({
                                key: `extra-${t}`,
                                icon: BACKEND_TAG_ICONS[t] || "mdi:tag-outline",
                                label: humanize(t),
                                selected: true,
                                onDelete: () => {
                                  setTags((prev) =>
                                    prev.filter((x) => x !== t),
                                  );
                                  markDirty();
                                },
                              }),
                            )}
                            {/* Curated tags (selected or not) */}
                            {EVAL_TAGS.map((tag) => {
                              const selected = tags.includes(tag.value);
                              return renderChip({
                                key: tag.value,
                                icon: tag.icon,
                                label: tag.label,
                                selected,
                                onClick: () => {
                                  setTags((prev) =>
                                    selected
                                      ? prev.filter((t) => t !== tag.value)
                                      : [...prev, tag.value],
                                  );
                                  markDirty();
                                },
                              });
                            })}
                          </>
                        );
                      })()}
                    </Box>
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
                <Box sx={{ flex: 1, overflow: "auto", minHeight: 0 }}>
                  <TestPlayground
                    ref={testPlaygroundRef}
                    templateId={evalId}
                    model={model}
                    instructions={
                      evalType === "code"
                        ? ""
                        : evalType === "llm"
                          ? messages.map((m) => m.content || "").join("\n")
                          : instructions
                    }
                    evalType={evalType}
                    requiredKeys={variables}
                    showVersions={!(isSystemEval && evalType === "code")}
                    errorLocalizerEnabled={errorLocalizerEnabled}
                    onTestResult={handleTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    onVersionSelect={handleVersionSelect}
                    isComposite={isComposite}
                    templateFormat={templateFormat}
                    functionParamsSchema={
                      evalData?.config?.function_params_schema ||
                      evalData?.config?.functionParamsSchema ||
                      null
                    }
                    configParamsDesc={
                      evalData?.config?.config_params_desc ||
                      evalData?.config?.configParamsDesc ||
                      null
                    }
                    onSourceTabChange={() => {
                      setTestError(null);
                      setTestPassed(false);
                    }}
                    onClearResult={() => {
                      setTestError(null);
                      setTestPassed(false);
                    }}
                    onReadyChange={setIsPlaygroundReady}
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
                    mt: "auto",
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
                  {isDirty && (
                    <Typography
                      variant="caption"
                      color="warning.main"
                      sx={{ fontSize: "11px" }}
                    >
                      Unsaved changes
                    </Typography>
                  )}

                  <Tooltip
                    title={
                      !isPlaygroundReady && !isTesting
                        ? "Map all required keys before running"
                        : ""
                    }
                    placement="top"
                  >
                    <span>
                      <Button
                        variant={isComposite ? "contained" : "outlined"}
                        size="small"
                        onClick={handleTestEvaluation}
                        disabled={isTesting || !isPlaygroundReady}
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
                        {isTesting
                          ? "Running..."
                          : isComposite
                            ? "Run Composite"
                            : "Test Evaluation"}
                      </Button>
                    </span>
                  </Tooltip>
                  {!isSystemEval && !isComposite && (
                    <Button
                      variant="contained"
                      size="small"
                      onClick={handleSaveVersion}
                      disabled={isSaving}
                      startIcon={
                        isSaving ? (
                          <CircularProgress size={14} />
                        ) : (
                          <Iconify icon="mdi:content-save-outline" width={16} />
                        )
                      }
                      sx={{ textTransform: "none" }}
                    >
                      {isSaving ? "Saving..." : "Save Version"}
                    </Button>
                  )}
                  {!isSystemEval && isComposite && (
                    <Button
                      variant="contained"
                      size="small"
                      onClick={handleSaveComposite}
                      disabled={isSaving || !isDirty}
                      startIcon={
                        isSaving ? (
                          <CircularProgress size={14} />
                        ) : (
                          <Iconify icon="mdi:content-save-outline" width={16} />
                        )
                      }
                      sx={{ textTransform: "none" }}
                    >
                      {isSaving ? "Saving..." : "Save Changes"}
                    </Button>
                  )}
                </Box>
              </Box>
            }
          />
        </Box>
      )}

      {/* ═══ Usage Tab ═══ */}
      {activeTab === "usage" && (
        <Box sx={{ flex: 1, minHeight: 0 }}>
          <EvalUsageTab
            templateId={evalId}
            outputType={outputType}
            evalType={evalType}
          />
        </Box>
      )}

      {/* ═══ Feedback Tab ═══ */}
      {activeTab === "feedback" && (
        <Box sx={{ flex: 1, minHeight: 0 }}>
          <EvalFeedbackTab templateId={evalId} />
        </Box>
      )}

      {/* ═══ Ground Truth Tab ═══ */}
      {activeTab === "ground_truth" && (
        <Box sx={{ flex: 1, minHeight: 0 }}>
          <EvalGroundTruthTab templateId={evalId} />
        </Box>
      )}
    </Box>
  );
};

export default EvalDetailPage;

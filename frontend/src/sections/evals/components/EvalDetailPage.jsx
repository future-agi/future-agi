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
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { LoadingScreen } from "src/components/loading-screen";
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
import {
  useFeatureLocked,
  CAPABILITY,
  useFeatureDenied,
} from "src/hooks/useCapabilities";
import { useErrorLocalizationAvailable } from "src/hooks/useErrorLocalization";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import axios, { endpoints } from "src/utils/axios";

import {
  useEvalDetail,
  useUpdateEval,
  useDuplicateEval,
} from "../hooks/useEvalDetail";
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
import { buildCompositeChildConfigs } from "../Helpers/compositeRuntimeConfig";
import {
  compactDefinition,
  unsavedTestTooltip,
} from "../utils/unsavedDefinition";
import EvalFeedbackTab from "./EvalFeedbackTab";
import {
  isTypedEvalType,
  typedEvalLabel,
} from "src/sections/common/EvalTypes/typedEvalTypes";
import { versionChipOf } from "./versionChip";
import TypedEvalDetail from "../types/TypedEvalDetail";
import { hasTypedDetail } from "../types/typedViews";
import AgreementTab from "../alignment/AgreementTab";
import {
  compositeExtrasBody,
  rowsFromDetail,
  validateComposite,
} from "src/sections/common/EvalTypes/compositeModel";
import EvalGroundTruthTab from "./EvalGroundTruthTab";
import { useGroundTruthList } from "../hooks/useGroundTruth";
import EvalUsageTab from "./EvalUsageTab";
import VersionBadge from "./VersionBadge";
import BulkDeleteDialog from "./BulkDeleteDialog";
import { EVAL_TAGS } from "../constant";
import JevOutputConfig from "./JevOutputConfig";
import {
  buildJevMapping,
  getJevValidationErrors,
  hasExtraJevMessages,
  orderChoiceScores,
} from "../utils/jevMapping";
import { FAGI_MODEL_VALUES, JEV_MODEL_VALUES } from "./ModelSelector";
import { buildDataInjection } from "src/sections/common/EvalPicker/evalPickerConfigUtils";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import AllowNaSwitch from "src/sections/common/EvalPicker/AllowNaSwitch";
import { allowNaApplies } from "src/sections/common/EvalPicker/allowNaModel";
import { isDecisionModel } from "src/sections/common/DecisionJudges/decisionModels";

const ERROR_LOCALIZER_LOCKED_TOOLTIP =
  "Error Localization isn't enabled for this workspace.";

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
  if (data_injection.full_row || data_injection.fullRow)
    opts.push("dataset_row");
  if (data_injection.span_context || data_injection.spanContext)
    opts.push("span_context");
  if (data_injection.trace_context || data_injection.traceContext)
    opts.push("trace_context");
  if (data_injection.session_context || data_injection.sessionContext)
    opts.push("session_context");
  if (data_injection.call_context || data_injection.callContext)
    opts.push("call_context");
  if (opts.length > 0) return opts;
  if (
    data_injection.variables_only === false ||
    data_injection.variablesOnly === false
  ) {
    return ["full_row"];
  }
  return ["variables_only"];
};

const getEvalPromptText = (evalData, config = {}) =>
  evalData?.instructions || config?.rule_prompt || "";

const EvalDetailPage = () => {
  const { evalId } = useParams();
  const navigate = useNavigate();
  const { role } = useAuthContext();
  const canEditEvals =
    RolePermission.EVALS[PERMISSIONS.EDIT_CREATE_DELETE_EVALS][role];
  const [searchParams, setSearchParams] = useSearchParams();
  const { enqueueSnackbar } = useSnackbar();
  // Fail closed while capabilities load (both flags true) so gated controls
  // never flash as available before the fetch resolves.
  const { locked: fagiLocked, isLoading: capsLoading } = useFeatureLocked(
    CAPABILITY.TURING_MODELS,
  );
  const { locked: agentEvalLocked } = useFeatureLocked(CAPABILITY.AGENTIC_EVAL);
  // Only a confirmed denial locks Jev: not while capabilities load, and not
  // when their fetch fails (self-hosted always allows Jev; the server checks).
  const jevLocked = useFeatureDenied(CAPABILITY.JEV_MODELS);
  const errorLocalizerAvailable = useErrorLocalizationAvailable();
  // Confirmed denial (loaded AND not allowed). Seed the default model raw and
  // only strip it on confirmed denial, so entitled users don't lose the
  // "turing_large" default while capabilities are still loading.
  const fagiModelsDenied = fagiLocked && !capsLoading;

  const {
    data: evalData,
    isLoading,
    error: fetchError,
  } = useEvalDetail(evalId);
  const updateEval = useUpdateEval(evalId);
  const duplicateEval = useDuplicateEval(evalId);
  const createVersion = useCreateEvalVersion(evalId);
  const { data: versionsData } = useEvalVersions(evalId);
  const testPlaygroundRef = useRef(null);

  // Editable fields
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
  const [jevMapping, setJevMapping] = useState({});
  const [outputType, setOutputType] = useState("pass_fail");
  const [passThreshold, setPassThreshold] = useState(0.5);
  // W1-4: a workspace judge may answer N/A (the template's allow_na).
  const [allowNa, setAllowNa] = useState(false);
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
  const errorLocalizerActive =
    errorLocalizerEnabled && !agentEvalLocked && errorLocalizerAvailable;

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
  // Variable→column mapping from the active test-panel tab. Used by the
  // InstructionEditor / LLMPromptEditor to highlight mapped variables in
  // green instead of leaving them red after the user binds them.
  const [playgroundMapping, setPlaygroundMapping] = useState({});
  const handlePlaygroundReadyChange = useCallback((ready, mapping) => {
    setIsPlaygroundReady(!!ready);
    if (mapping && typeof mapping === "object") {
      setPlaygroundMapping(mapping);
    }
  }, []);

  // Auto-dismiss test error after 6 seconds
  useEffect(() => {
    if (!testError) return;
    const timer = setTimeout(() => setTestError(null), 6000);
    return () => clearTimeout(timer);
  }, [testError]);

  const handleTestResult = useCallback((success, result) => {
    // Only a run that returned a result reads "Test completed"; a failed one
    // must not, also once its error caption above auto-dismisses.
    setTestPassed(Boolean(success));
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

  useEffect(() => {
    if (!viewingVersion || !versionsData?.versions) return;
    const fresh = versionsData.versions.find((v) => v.id === viewingVersion.id);
    if (!fresh) return;
    const freshFlag = fresh.is_default ?? fresh.isDefault ?? false;
    const localFlag =
      viewingVersion.is_default ?? viewingVersion.isDefault ?? false;
    if (freshFlag !== localFlag) {
      setViewingVersion((prev) =>
        prev ? { ...prev, is_default: freshFlag, isDefault: freshFlag } : prev,
      );
    }
  }, [versionsData, viewingVersion]);

  const defaultVersion = useMemo(() => {
    const list = versionsData?.versions || [];
    if (!list.length) return null;
    const byFlag = list.find((v) => v.is_default || v.isDefault);
    if (byFlag) return byFlag;
    // matches backend get_default: highest version_number when none flagged
    return [...list].sort(
      (a, b) =>
        (b.version_number ?? b.versionNumber ?? Number.MIN_SAFE_INTEGER) -
        (a.version_number ?? a.versionNumber ?? Number.MIN_SAFE_INTEGER),
    )[0];
  }, [versionsData]);

  const handleVersionSelect = useCallback(
    (version) => {
      const versionToLoad = version || defaultVersion;
      // Every selection reloads config, including deselection to the default.
      if (isDirty) {
        if (
          !window.confirm(
            "You have unsaved changes. Discard and switch version?",
          )
        )
          return false;
      }

      if (!versionToLoad) {
        // Deselect — restore to current eval data
        isPopulatingRef.current = true;
        setViewingVersion(null);
        setSearchParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            next.delete("v");
            return next;
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
          setModel(config.model || evalData.model || "turing_large");
          setJevMapping(config.jev_mapping || {});
          setOutputType(
            evalData.output_type ||
              evalData.output_type_normalized ||
              "pass_fail",
          );
          setPassThreshold(evalData.pass_threshold ?? 0.5);
          setAllowNa(Boolean(config.allow_na ?? evalData.config?.allow_na));
          // Derive choiceScores: use the backend's scored dict if present,
          // otherwise convert the `choices` array (used by deterministic /
          // multi-class evals like tone, customer_agent_*) into a default
          // scored map so the UI renders the choices.
          {
            const scored = evalData.choice_scores;
            if (scored && Object.keys(scored).length > 0) {
              // Authored order (see the initial-load effect below).
              setChoiceScores(
                orderChoiceScores(
                  scored,
                  config.jev_mapping?.choice?.labels || evalData.choices,
                ),
              );
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
          setFewShotExamples(
            config.few_shot_examples || config.fewShotExamples || [],
          );
        }
        setIsDirty(false);
        setTimeout(() => {
          isPopulatingRef.current = false;
        }, 0);
        return true;
      }

      // Load version config into the form
      isPopulatingRef.current = true;
      setViewingVersion(versionToLoad);
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("v", String(versionToLoad.version_number));
          return next;
        },
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
              config: c.config || {},
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
        return true;
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
      setModel(config.model || versionToLoad.model || "turing_large");
      setJevMapping(config.jev_mapping || {});
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
      // Keep the selected version N/A rule and Jev choice order.
      setAllowNa(Boolean(config.allow_na ?? evalData?.config?.allow_na));
      setChoiceScores(
        orderChoiceScores(
          config.choiceScores || config.choice_scores || {},
          config.jev_mapping?.choice?.labels || config.choices,
        ),
      );
      setMultiChoice(config.multiChoice ?? config.multi_choice ?? false);
      if (config.messages && config.messages.length > 0) {
        setMessages(config.messages);
      } else if (evalData.eval_type === "llm" && promptText) {
        setMessages([{ role: "system", content: promptText }]);
      }
      setFewShotExamples(
        config.few_shot_examples || config.fewShotExamples || [],
      );
      setCheckInternet(config.check_internet ?? false);
      setAgentMode(config.agent_mode || "agent");
      setSummaryType(resolve_summary_type(config.summary));
      setConnectorIds(extract_selected_tools(config.tools));
      setKnowledgeBaseIds(
        Array.isArray(config.knowledge_bases) ? config.knowledge_bases : [],
      );
      setContextOptions(resolve_context_options(config.data_injection));
      setErrorLocalizerEnabled(config.error_localizer_enabled ?? false);
      // Description and tags belong to the eval, not to a version snapshot.
      // A custom eval always opens on a version, so load them here too, or
      // the page shows them empty and Save Version clears the tags.
      setDescription(evalData?.description || "");
      setTags(evalData?.tags || evalData?.eval_tags || []);
      setIsDirty(false);
      setTimeout(() => {
        isPopulatingRef.current = false;
      }, 0);
      return true;
    },
    [defaultVersion, evalData, isDirty, isComposite, setSearchParams],
  );

  // Three-dot menu
  const [menuAnchor, setMenuAnchor] = useState(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteLoading, setDeleteLoading] = useState(false);

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
      const isCustom = evalData.owner !== "system";
      const urlVersion = searchParams.get("v");
      if (urlVersion && !initialLoadDone.current) {
        if (!versionsData) return;
        const match = (versionsData?.versions || []).find(
          (ver) => String(ver.version_number) === String(urlVersion),
        );
        if (match) {
          initialLoadDone.current = true;
          handleVersionSelect(match);
          return;
        }
      }
      if (isCustom && !urlVersion && !initialLoadDone.current) {
        if (!versionsData) return;
        if (defaultVersion) {
          initialLoadDone.current = true;
          handleVersionSelect(defaultVersion);
          return;
        }
      }
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
        setModel(config.model || evalData.model || "turing_large");
        setJevMapping(config.jev_mapping || {});
        setOutputType(
          evalData.output_type ||
            evalData.output_type_normalized ||
            "pass_fail",
        );
        setPassThreshold(evalData.pass_threshold ?? 0.5);
        setAllowNa(Boolean(config.allow_na ?? evalData.config?.allow_na));
        // Derive choiceScores: use the backend's scored dict if present,
        // otherwise convert the `choices` array (used by deterministic /
        // multi-class evals like tone, customer_agent_*) into a default
        // scored map so the UI renders the choices.
        {
          const scored = evalData.choice_scores;
          if (scored && Object.keys(scored).length > 0) {
            // jsonb returns choice_scores keys shortest first; show and save
            // them in the order the author saved.
            setChoiceScores(
              orderChoiceScores(
                scored,
                config.jev_mapping?.choice?.labels || evalData.choices,
              ),
            );
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
        setFewShotExamples(
          config.few_shot_examples || config.fewShotExamples || [],
        );
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
  }, [evalData, versionsData, defaultVersion]); // eslint-disable-line react-hooks/exhaustive-deps

  const evalType = evalData?.eval_type || "llm";
  // W7: a typed eval (decision tree, remote scorer) shows its own Details.
  const showTypedDetail = isTypedEvalType(evalType) && hasTypedDetail(evalType);
  // W7: agreement with reviewers, for every eval type, once real.
  const agreementTabs = AgreementTab.ready ? ["agreement"] : [];
  const isSystemEval = evalData?.owner === "system";
  // W1-4: where the scorer honours it (the workspace's own judges).
  const allowNaShown = allowNaApplies({
    evalType,
    isSystemEval,
    decisionModel: isDecisionModel(model) ? model : null,
    supportsNa: evalData?.supports_na,
  });

  const hasDataInjection =
    evalType === "agent" &&
    Array.isArray(contextOptions) &&
    contextOptions.some((o) => o && o !== "variables_only");

  const hasTemplateVariable =
    templateFormat === "jinja"
      ? extractJinjaVariables(instructions).length > 0
      : /\{\{\s*[^{}]+?\s*\}\}/.test(instructions);

  const needsTemplateVariable =
    evalType !== "code" &&
    !hasDataInjection &&
    !isComposite &&
    !hasTemplateVariable;

  const variableTooltip = !instructions?.trim()
    ? "Instructions are required"
    : needsTemplateVariable
      ? `Instructions must contain at least one ${templateFormat === "jinja" ? "Jinja" : "Mustache"} variable (e.g. {{input}})`
      : "";

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
    // W7-5: each child with its stored seat, judge and level.
    setCompositeChildren(rowsFromDetail(compositeDetail));
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
    updateEval.isPending ||
    createVersion.isPending ||
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

  const isJevModel = JEV_MODEL_VALUES.has(model);
  const { data: groundTruthData } = useGroundTruthList(
    isJevModel && !isComposite && evalType !== "code" ? evalId : null,
  );
  const effectiveConfig = useMemo(
    () =>
      viewingVersion
        ? viewingVersion.config_snapshot || viewingVersion.configSnapshot || {}
        : evalData?.config || {},
    [viewingVersion, evalData],
  );
  const jevPayload = useMemo(
    () =>
      isJevModel
        ? { jev_mapping: buildJevMapping(outputType, choiceScores, jevMapping) }
        : {},
    [isJevModel, outputType, choiceScores, jevMapping],
  );
  const jevMessages =
    evalType === "llm" ? messages : effectiveConfig.messages || [];
  const jevErrors = isJevModel
    ? getJevValidationErrors({
        mapping: jevPayload.jev_mapping,
        groundTruthEnabled: (groundTruthData?.items || []).some(
          (item) => item.is_active && item.enabled,
        ),
        config: {
          ...effectiveConfig,
          // Only agent evals use agent_mode; an LLM judge drafted on the
          // Agents tab keeps a stale "agent" value that the backend ignores.
          agent_mode: undefined,
          check_internet: checkInternet,
          few_shot_examples: fewShotExamples,
          ...(evalType === "agent"
            ? {
                agent_mode: agentMode,
                tools: build_tools_payload(connectorIds),
                knowledge_bases: knowledgeBaseIds,
                data_injection: buildDataInjection(contextOptions),
              }
            : {}),
        },
        multiChoice,
        choiceScores,
        inputDataTypes: evalData?.input_data_types,
        messages: jevMessages,
      })
    : [];
  const jevError = jevErrors[0];
  const jevTestBlocked = Boolean(jevError || (isJevModel && jevLocked));

  // What Test runs: the definition on screen, sent with the playground
  // request and never saved (Save Version saves it). The template update's
  // fields, so a Test runs exactly what Save would save.
  const unsavedDefinition = useMemo(() => {
    if (!evalData || isSystemEval || isComposite) return null;
    return compactDefinition({
      ...jevPayload,
      instructions: evalType === "code" ? undefined : instructions || undefined,
      code: evalType === "code" ? code : undefined,
      code_language: evalType === "code" ? codeLanguage : undefined,
      model: evalType === "code" ? undefined : model,
      output_type: outputType,
      pass_threshold: passThreshold,
      choice_scores:
        Object.keys(choiceScores || {}).length > 0 ? choiceScores : null,
      multi_choice: multiChoice,
      check_internet: checkInternet,
      mode: evalType === "agent" ? agentMode : undefined,
      tools:
        evalType === "agent" ? build_tools_payload(connectorIds) : undefined,
      knowledge_bases: evalType === "agent" ? knowledgeBaseIds : undefined,
      data_injection:
        evalType === "agent" ? buildDataInjection(contextOptions) : undefined,
      summary:
        evalType === "agent"
          ? summaryType === "custom"
            ? { type: "custom", custom: "" }
            : { type: summaryType }
          : undefined,
      error_localizer_enabled: errorLocalizerActive,
      template_format: templateFormat,
      messages: evalType === "llm" ? messages : undefined,
      few_shot_examples: evalType === "llm" ? fewShotExamples : undefined,
    });
  }, [
    evalData,
    isSystemEval,
    isComposite,
    jevPayload,
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
    connectorIds,
    knowledgeBaseIds,
    contextOptions,
    summaryType,
    errorLocalizerActive,
    templateFormat,
    messages,
    fewShotExamples,
  ]);

  // A composite with unsaved edits tests them through the execute-adhoc
  // request (as the create page does), never by saving them. That request
  // carries no pinned child versions, jury or checks, so a composite with
  // any of those tests as saved until Save Changes.
  const compositeExtras = compositeExtrasBody(
    compositeChildren,
    compositeChildWeights,
  );
  const compositeTestsSaved =
    isComposite &&
    (compositeChildren.some((c) => c.pinned_version_id) ||
      Boolean(evalData?.config?.jury) ||
      Object.keys(compositeExtras).length > 0);
  const compositeTestConfig = useMemo(() => {
    if (!isComposite || isSystemEval || !isDirty || compositeTestsSaved) {
      return null;
    }
    const weights = {};
    compositeChildren.forEach((c) => {
      const w = compositeChildWeights[c.child_id];
      if (w != null) weights[c.child_id] = w;
    });
    return {
      child_template_ids: compositeChildren.map((c) => c.child_id),
      child_configs: buildCompositeChildConfigs(compositeChildren),
      aggregation_enabled: compositeAggEnabled,
      aggregation_function: compositeAggFunction,
      composite_child_axis: compositeChildAxis || "",
      child_weights: Object.keys(weights).length > 0 ? weights : null,
      pass_threshold: evalData?.pass_threshold ?? 0.5,
    };
  }, [
    isComposite,
    isSystemEval,
    isDirty,
    compositeTestsSaved,
    compositeChildren,
    compositeChildWeights,
    compositeAggEnabled,
    compositeAggFunction,
    compositeChildAxis,
    evalData,
  ]);
  // System evaluations are read-only: their Test sends only the picked model.
  const testsUnsavedChanges = isComposite
    ? Boolean(compositeTestConfig)
    : isDirty && Boolean(unsavedDefinition);

  // Save version
  const handleSaveVersion = useCallback(async () => {
    if (jevError) {
      enqueueSnackbar(jevError, { variant: "error" });
      return;
    }
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
    if (fagiLocked && evalType !== "code" && !model && !isComposite) {
      enqueueSnackbar("Please select a model.", { variant: "error" });
      setOpenModelMenuSignal((n) => n + 1);
      return;
    }
    try {
      const dataInjection = buildDataInjection(contextOptions);
      const summary =
        summaryType === "custom"
          ? { type: "custom", custom: "" }
          : { type: summaryType };
      const tools = build_tools_payload(connectorIds);
      // Update the template first
      const payload = {
        ...jevPayload,
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
        description: description || null,
        tags,
        check_internet: checkInternet,
        mode: evalType === "agent" ? agentMode : undefined,
        tools: evalType === "agent" ? tools : undefined,
        knowledge_bases: evalType === "agent" ? knowledgeBaseIds : undefined,
        data_injection: evalType === "agent" ? dataInjection : undefined,
        summary: evalType === "agent" ? summary : undefined,
        error_localizer_enabled: errorLocalizerActive,
        template_format: templateFormat,
        messages: evalType === "llm" ? messages : undefined,
        // Send [] for LLM evals so the BE can persist a user-cleared list.
        // Omitting on empty would leave the previous examples in place.
        few_shot_examples: evalType === "llm" ? fewShotExamples : undefined,
        allow_na: allowNaShown ? allowNa : undefined,
      };
      await updateEval.mutateAsync(payload);

      // Build a config snapshot for the version so it captures the full state
      const configSnapshot = {
        ...(isJevModel ? effectiveConfig : evalData?.config || {}),
        ...jevPayload,
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
        error_localizer_enabled: errorLocalizerActive,
        template_format: templateFormat,
        messages: evalType === "llm" ? messages : undefined,
        few_shot_examples: evalType === "llm" ? fewShotExamples : undefined,
        allow_na: allowNaShown ? allowNa : undefined,
      };
      const newVersion = await createVersion.mutateAsync({
        config_snapshot: configSnapshot,
        criteria: evalType === "code" ? code : instructions,
        model,
      });
      enqueueSnackbar(`Version V${newVersion?.version_number ?? ""} saved`, {
        variant: "success",
      });
      setIsDirty(false);
      // Switch to viewing the newly created version
      if (newVersion?.version_number) {
        setViewingVersion({ ...newVersion, config_snapshot: configSnapshot });
        setSearchParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            next.set("v", String(newVersion.version_number));
            return next;
          },
          { replace: true },
        );
        // Switch to versions tab and highlight the new version
        testPlaygroundRef.current?.switchToVersion?.(newVersion.id);
      } else {
        setViewingVersion(null);
      }
    } catch (err) {
      enqueueSnackbar(
        getSafeActionErrorMessage(err, "Failed to save version"),
        { variant: "error" },
      );
    }
  }, [
    jevError,
    jevPayload,
    isJevModel,
    effectiveConfig,
    templateFormat,
    isComposite,
    evalType,
    agentEvalLocked,
    fagiLocked,
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
    errorLocalizerActive,
    messages,
    fewShotExamples,
    allowNa,
    allowNaShown,
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
        child_configs: buildCompositeChildConfigs(compositeChildren),
        child_weights: Object.keys(weights).length > 0 ? weights : null,
        // W7-5: jury seats, levels and checks.
        ...compositeExtrasBody(compositeChildren, compositeChildWeights),
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
      enqueueSnackbar(
        getSafeActionErrorMessage(err, "Failed to save composite"),
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

  // Test runs what is on screen and saves nothing: the playground gets the
  // unsaved definition (unsavedDefinition / compositeTestConfig) with its
  // request. Only Save Version / Save Changes change what the evaluation,
  // its versions and its experiments use.
  const handleTestEvaluation = useCallback(() => {
    if (jevTestBlocked) {
      enqueueSnackbar(jevError || "Not included in your plan", {
        variant: "error",
      });
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
    try {
      testPlaygroundRef.current?.runTest?.(evalId);
    } catch (error) {
      handleTestResult(
        false,
        getSafeActionErrorMessage(error, "Failed to run test"),
      );
      setIsTesting(false);
    }
  }, [
    jevError,
    jevTestBlocked,
    enqueueSnackbar,
    evalId,
    evalType,
    fagiLocked,
    isComposite,
    model,
    handleTestResult,
  ]);

  // Delete
  const handleDeleteClick = useCallback(() => {
    setMenuAnchor(null);
    setDeleteDialogOpen(true);
  }, []);

  const handleDeleteCancel = useCallback(() => {
    if (!deleteLoading) {
      setDeleteDialogOpen(false);
    }
  }, [deleteLoading]);

  const handleDeleteConfirm = useCallback(async () => {
    setDeleteLoading(true);
    try {
      await axios.post(endpoints.develop.eval.deleteEvalsTemplate, {
        eval_template_id: evalId,
      });
      enqueueSnackbar("Evaluation deleted", { variant: "success" });
      navigate("/dashboard/evaluations");
    } catch {
      enqueueSnackbar("Failed to delete evaluation", { variant: "error" });
      setDeleteLoading(false);
    }
  }, [evalId, enqueueSnackbar, navigate]);

  if (isLoading) {
    return <LoadingScreen sx={{ height: "100%", minHeight: "60vh" }} />;
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

  const typeRole = isComposite
    ? "info"
    : evalType === "code"
      ? "warning"
      : evalType === "agent"
        ? "success"
        : "discovery";

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
            version={versionChipOf({ viewingVersion, evalData, evalType })}
          />
          <Box
            sx={{
              px: 1,
              py: 0.25,
              borderRadius: "4px",
              fontSize: "12px",
              fontWeight: 600,
              backgroundColor: `status.${typeRole}.bg`,
              color: `status.${typeRole}.fg`,
            }}
          >
            {(() => {
              if (!isComposite) {
                // W7: Decision tree / Remote scorer.
                if (typedEvalLabel(evalType)) return typedEvalLabel(evalType);
                return evalType === "code"
                  ? "Code"
                  : evalType === "agent"
                    ? "Agent"
                    : "LLM";
              }
              const typeLabels = {
                code: "Code",
                agent: "Agent",
                llm: "LLM",
                // W7-5: a task-suite check child.
                check: "Check",
              };
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
          {isSystemEval ? (
            <Typography variant="caption" color="text.disabled">
              Read-only (system eval)
            </Typography>
          ) : (
            <>
              <IconButton
                size="small"
                onClick={(e) => setMenuAnchor(e.currentTarget)}
              >
                <Iconify icon="solar:menu-dots-bold" width={20} />
              </IconButton>
              <Menu
                anchorEl={menuAnchor}
                open={Boolean(menuAnchor)}
                onClose={() => setMenuAnchor(null)}
              >
                <CustomTooltip
                  show
                  type="black"
                  size="small"
                  title="Create an editable copy of this eval"
                  placement="left"
                  arrow
                >
                  <MenuItem
                    disabled={!canEditEvals}
                    onClick={() => {
                      setMenuAnchor(null);
                      duplicateEval.mutate(evalData?.name, {
                        onSuccess: (result) => {
                          enqueueSnackbar("Evaluation duplicated", {
                            variant: "success",
                          });
                          if (result?.eval_template_id)
                            navigate(
                              `/dashboard/evaluations/${result.eval_template_id}`,
                            );
                        },
                        onError: () =>
                          enqueueSnackbar("Failed to duplicate evaluation", {
                            variant: "error",
                          }),
                      });
                    }}
                  >
                    <Iconify icon="solar:copy-bold" width={16} sx={{ mr: 1 }} />{" "}
                    Duplicate
                  </MenuItem>
                </CustomTooltip>
                <CustomTooltip
                  show
                  type="black"
                  size="small"
                  title="Permanently delete this eval"
                  placement="left"
                  arrow
                >
                  <MenuItem
                    disabled={!canEditEvals}
                    onClick={handleDeleteClick}
                  >
                    <Iconify
                      icon="solar:trash-bin-trash-bold"
                      width={16}
                      sx={{ mr: 1 }}
                    />{" "}
                    Delete
                  </MenuItem>
                </CustomTooltip>
              </Menu>
            </>
          )}
        </Box>
      </Box>

      <BulkDeleteDialog
        open={deleteDialogOpen}
        count={1}
        onConfirm={handleDeleteConfirm}
        onCancel={handleDeleteCancel}
        isLoading={deleteLoading}
      />

      {/* Top Tabs */}
      <SegmentedTabs
        value={activeTab}
        onChange={handleTabChange}
        sx={{ mb: 1.5, flexShrink: 0, width: "fit-content" }}
      >
        {/* W7: a typed eval (a decision tree, a remote scorer) reads no
            feedback or ground truth, so it has the code/composite tabs. */}
        {(evalType === "code" || isComposite || isTypedEvalType(evalType)
          ? ["details", "usage", ...agreementTabs]
          : ["details", "usage", "feedback", "ground_truth", ...agreementTabs]
        ).map((tab) => {
          const labels = {
            details: "Eval Details",
            usage: "Usage",
            feedback: "Feedback",
            ground_truth: "Ground Truth",
            agreement: "Agreement",
          };
          return <Tab key={tab} value={tab} label={labels[tab]} />;
        })}
      </SegmentedTabs>

      {/* ═══ W7: a typed eval's own Details ═══ */}
      {activeTab === "details" && showTypedDetail && (
        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          <TypedEvalDetail
            evalType={evalType}
            templateId={evalId}
            evalData={evalData}
          />
        </Box>
      )}

      {/* ═══ Eval Details Tab — playground layout ═══ */}
      {activeTab === "details" && !showTypedDetail && (
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
                        backgroundColor: "status.discovery.bg",
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
                        <strong>V{viewingVersion.version_number}</strong>{" "}
                        config. Edit and save to create a new version.
                      </Typography>
                      <Button
                        size="small"
                        variant="text"
                        onClick={() => handleVersionSelect(null)}
                        sx={{
                          minWidth: 0,
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
                    templateId={evalId}
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
                    openModelMenuSignal={openModelMenuSignal}
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
                    mappedVariables={playgroundMapping}
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
                      openModelMenuSignal={openModelMenuSignal}
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
                      modelSelectorDisabled={false}
                    />
                    <FewShotExamples
                      isJevModel={isJevModel}
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

                {isJevModel && !isComposite && evalType !== "code" && (
                  <JevOutputConfig
                    outputType={outputType}
                    choiceScores={choiceScores}
                    mapping={jevMapping}
                    onChange={(next) => {
                      setJevMapping(next);
                      markDirty();
                    }}
                    errors={jevErrors}
                    hasExtraMessages={hasExtraJevMessages(jevMessages)}
                    disabled={isSystemEval}
                    locked={jevLocked}
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
                          value={Math.round(passThreshold * 100)}
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
                      isJevModel={isJevModel}
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
                {allowNaShown && !isComposite && (
                  <AllowNaSwitch
                    checked={allowNa}
                    onChange={(v) => {
                      setAllowNa(v);
                      markDirty();
                    }}
                  />
                )}

                {errorLocalizerAvailable &&
                  !isComposite &&
                  evalType !== "code" && (
                    <Box>
                      <CustomTooltip
                        show={agentEvalLocked}
                        type=""
                        arrow
                        title={ERROR_LOCALIZER_LOCKED_TOOLTIP}
                      >
                        <Box sx={{ display: "inline-flex" }}>
                          <FormControlLabel
                            control={
                              <Checkbox
                                checked={errorLocalizerActive}
                                disabled={agentEvalLocked}
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
                        </Box>
                      </CustomTooltip>
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
                          SAFETY: "solar:shield-warning-linear",
                          RAG: "solar:database-linear",
                          HALLUCINATION: "solar:danger-circle-linear",
                          CONVERSATION: "solar:chat-round-line-linear",
                          CHAT: "solar:bot-linear",
                          AUDIO: "solar:volume-loud-linear",
                          TEXT: "solar:notes-linear",
                          IMAGE: "solar:gallery-linear",
                          VIDEO: "solar:videocamera-linear",
                          PDF: "mdi:file-pdf-box",
                          FUNCTION: "solar:programming-linear",
                          LLMS: "solar:brain-linear",
                          FUTURE_EVALS: "solar:stars-minimalistic-linear",
                          CUSTOM: "solar:pen-linear",
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
                              cursor: isSystemEval ? "default" : "pointer",
                            }}
                          />
                        );

                        return (
                          <>
                            {/* Selected backend-only tags first (always "selected") */}
                            {extraTags.map((t) =>
                              renderChip({
                                key: `extra-${t}`,
                                icon:
                                  BACKEND_TAG_ICONS[t] || "solar:tag-linear",
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
                    code={code}
                    codeLanguage={codeLanguage}
                    isSystemEval={isSystemEval}
                    requiredKeys={variables}
                    multiChoice={multiChoice}
                    showVersions={!(isSystemEval && evalType === "code")}
                    errorLocalizerEnabled={errorLocalizerActive}
                    onTestResult={handleTestResult}
                    onColumnsLoaded={handleColumnsLoaded}
                    onVersionSelect={handleVersionSelect}
                    isComposite={isComposite}
                    unsavedDefinition={unsavedDefinition}
                    compositeAdhocConfig={compositeTestConfig}
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
                    onReadyChange={handlePlaygroundReadyChange}
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
                      color="status.danger.fg"
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
                        icon="solar:check-circle-bold"
                        width={16}
                        sx={{ color: "status.success.fg" }}
                      />
                      <Typography
                        variant="caption"
                        color="status.success.fg"
                        sx={{ fontSize: "12px" }}
                      >
                        Test completed
                      </Typography>
                    </Box>
                  )}
                  {isDirty && (
                    <Tooltip
                      placement="top"
                      title={
                        testsUnsavedChanges
                          ? unsavedTestTooltip({
                              isComposite,
                              evalType,
                              isJevModel,
                            })
                          : isComposite && !isSystemEval
                            ? "Test runs the saved composite: pinned versions, jury seats and checks are tested once saved."
                            : ""
                      }
                    >
                      <Typography
                        variant="caption"
                        sx={{
                          fontSize: "12px",
                          color: "status.warning.fg",
                        }}
                      >
                        {testsUnsavedChanges
                          ? "Testing unsaved changes"
                          : "Unsaved changes"}
                      </Typography>
                    </Tooltip>
                  )}

                  <Tooltip
                    title={
                      variableTooltip ||
                      (!isPlaygroundReady && !isTesting
                        ? "Map all required keys before running"
                        : "")
                    }
                    placement="top"
                  >
                    <span>
                      <Button
                        variant="outlined"
                        size="small"
                        onClick={handleTestEvaluation}
                        disabled={
                          jevTestBlocked ||
                          isTesting ||
                          !isPlaygroundReady ||
                          needsTemplateVariable ||
                          !canEditEvals
                        }
                        startIcon={
                          isTesting ? (
                            <CircularProgress size={14} />
                          ) : (
                            <Iconify
                              icon="solar:play-circle-linear"
                              width={16}
                            />
                          )
                        }
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
                    <CustomTooltip
                      show={!!variableTooltip}
                      title={variableTooltip}
                      arrow
                      size="small"
                      type="black"
                      placement="top"
                    >
                      <span>
                        <Button
                          variant="contained"
                          size="small"
                          onClick={handleSaveVersion}
                          disabled={
                            Boolean(jevError) ||
                            isSaving ||
                            !isDirty ||
                            needsTemplateVariable ||
                            !canEditEvals
                          }
                          startIcon={
                            isSaving ? (
                              <CircularProgress size={14} />
                            ) : (
                              <Iconify
                                icon="solar:diskette-linear"
                                width={16}
                              />
                            )
                          }
                        >
                          {isSaving ? "Saving..." : "Save Version"}
                        </Button>
                      </span>
                    </CustomTooltip>
                  )}
                  {!isSystemEval && isComposite && (
                    <CustomTooltip
                      show={compositeChildren.length === 0}
                      title="Select at least one child evaluation"
                      arrow
                      size="small"
                      type="black"
                      placement="top"
                    >
                      <span>
                        <Button
                          variant="contained"
                          size="small"
                          onClick={handleSaveComposite}
                          disabled={
                            isSaving ||
                            !isDirty ||
                            compositeChildren.length === 0 ||
                            !canEditEvals ||
                            // W7-5: seats need a judge; vote rules need weights.
                            validateComposite({
                              aggregation: compositeAggEnabled
                                ? compositeAggFunction
                                : null,
                              children: compositeChildren,
                              weights: compositeChildWeights,
                            }).length > 0
                          }
                          startIcon={
                            isSaving ? (
                              <CircularProgress size={14} />
                            ) : (
                              <Iconify
                                icon="solar:diskette-linear"
                                width={16}
                              />
                            )
                          }
                        >
                          {isSaving ? "Saving..." : "Save Changes"}
                        </Button>
                      </span>
                    </CustomTooltip>
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

      {/* ═══ W7: Agreement Tab ═══ */}
      {activeTab === "agreement" && (
        <Box sx={{ flex: 1, minHeight: 0 }}>
          <AgreementTab templateId={evalId} evalData={evalData} />
        </Box>
      )}

      {/* ═══ Ground Truth Tab ═══ */}
      {activeTab === "ground_truth" && (
        <Box sx={{ flex: 1, minHeight: 0 }}>
          <EvalGroundTruthTab
            templateId={evalId}
            isJevModel={isJevModel}
            onSwitchToDetails={() => setActiveTab("details")}
          />
        </Box>
      )}
    </Box>
  );
};

export default EvalDetailPage;

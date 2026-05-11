import {
  Box,
  Chip,
  CircularProgress,
  IconButton,
  Menu,
  MenuItem,
  Select,
  Tab,
  Tabs,
  Tooltip,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { useSnackbar } from "notistack";
import { CreditExhaustionBanner } from "src/components/CreditExhaustionBanner";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { useCreditExhaustion } from "src/hooks/use-credit-exhaustion";
import axios, { endpoints } from "src/utils/axios";
import { extractJinjaVariables } from "src/utils/jinjaVariables";
import { canonicalEntries } from "src/utils/utils";
import { camelCaseToTitleCase } from "src/utils/utils";
import CodeEditor from "./CodeEditor";
import DatasetTestMode from "./DatasetTestMode";
import TracingTestMode from "./TracingTestMode";
import SimulationTestMode from "./SimulationTestMode";
import {
  useEvalVersions,
  useSetDefaultVersion,
  useRestoreVersion,
} from "../hooks/useEvalVersions";
import useErrorLocalizerPoll from "../hooks/useErrorLocalizerPoll";
import EvalResultDisplay from "./EvalResultDisplay";
import { buildCompositeRuntimeConfig } from "../Helpers/compositeRuntimeConfig";
import VersionBadge from "./VersionBadge";
import {
  useExecuteCompositeEval,
  useExecuteCompositeEvalAdhoc,
} from "../hooks/useCompositeEval";

const SOURCE_TABS = ["Dataset", "Tracing", "Simulation", "Custom"];

const camelizeKey = (key = "") => key.replace(/_([a-z])/g, (_, char) => char.toUpperCase());

const formatParamLabel = (key) => camelCaseToTitleCase(camelizeKey(key));

// ── Custom JSON Input with AI bar ──
const CustomJsonInput = ({
  variables,
  inputValues,
  onInputChange,
  instructions,
  onColumnsLoaded,
}) => {
  const [jsonText, setJsonText] = useState("");
  const [jsonError, setJsonError] = useState(null);
  const followUpRef = useRef(null);

  // AI bar state
  const [aiOpen, setAiOpen] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [hasResult, setHasResult] = useState(false);
  const [originalJson, setOriginalJson] = useState(null);

  // Extract keys from current JSON — include nested dot-notation paths
  const jsonKeys = React.useMemo(() => {
    try {
      const parsed = JSON.parse(jsonText);
      if (typeof parsed !== "object" || Array.isArray(parsed)) return [];
      const keys = [];
      const walk = (obj, prefix) => {
        for (const [k, v] of Object.entries(obj)) {
          const path = prefix ? `${prefix}.${k}` : k;
          keys.push(path);
          if (v && typeof v === "object" && !Array.isArray(v)) {
            walk(v, path);
          }
        }
      };
      walk(parsed, "");
      return keys;
    } catch {
      return [];
    }
  }, [jsonText]);

  // Variable → JSON key mapping state
  const [varMapping, setVarMapping] = useState({});

  // Notify parent of available keys for autocomplete
  useEffect(() => {
    if (!onColumnsLoaded) return;
    const cols = jsonKeys.map((k) => ({
      id: k,
      name: k,
      dataType: k.includes(".") ? "json_path" : "text",
    }));
    onColumnsLoaded(cols, {});
  }, [jsonKeys.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-map variables to JSON keys when names match
  useEffect(() => {
    if (!jsonKeys.length || !variables.length) return;
    setVarMapping((prev) => {
      const next = { ...prev };
      let changed = false;
      variables.forEach((v) => {
        if (next[v]) return;
        const exact = jsonKeys.find((k) => k === v);
        const ci =
          !exact && jsonKeys.find((k) => k.toLowerCase() === v.toLowerCase());
        // Also check if variable matches a nested path's last segment
        const suffix =
          !exact &&
          !ci &&
          jsonKeys.find((k) => {
            const last = k.split(".").pop();
            return last === v || last?.toLowerCase() === v.toLowerCase();
          });
        const match = exact || ci || suffix;
        if (match) {
          next[v] = match;
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [variables, jsonKeys]);

  // Sync JSON keys with variables — add new ones, remove stale ones
  useEffect(() => {
    // Parse current JSON
    let current = {};
    try {
      current = JSON.parse(jsonText);
    } catch {
      /* keep empty */
    }

    const varSet = new Set(variables);

    // Remove keys that are no longer in variables (only if their value is empty)
    let changed = false;
    const updated = {};
    for (const [k, v] of Object.entries(current)) {
      if (!varSet.has(k) && (v === "" || v === null || v === undefined)) {
        changed = true; // drop this stale key
      } else {
        updated[k] = v;
      }
    }

    // Add new variables that aren't already in the JSON
    variables.forEach((v) => {
      updated[v] = v in current ? current[v] : inputValues[v] || "";
      if (!(v in current)) changed = true;
    });

    // Check if any old keys were removed
    for (const key of Object.keys(current)) {
      if (!varSet.has(key)) {
        changed = true;
        break;
      }
    }

    if (changed) {
      const keys = Object.keys(updated);
      setJsonText(keys.length > 0 ? JSON.stringify(updated, null, 2) : "{\n}");
    }
  }, [variables]); // eslint-disable-line react-hooks/exhaustive-deps

  // Parse JSON and update individual input values
  const handleJsonChange = useCallback(
    (text) => {
      setJsonText(text);
      try {
        const parsed = JSON.parse(text);
        setJsonError(null);
        if (typeof parsed === "object" && !Array.isArray(parsed)) {
          Object.entries(parsed).forEach(([k, v]) => {
            const val =
              typeof v === "object" ? JSON.stringify(v) : String(v ?? "");
            onInputChange(k, val);
          });
        }
      } catch {
        setJsonError("Invalid JSON");
      }
    },
    [onInputChange],
  );

  // Call AI
  const callAI = useCallback(
    async (userPrompt) => {
      const varList = variables.join(", ");
      const currentData =
        jsonText && jsonText.trim() !== "{}" ? jsonText : null;
      const description = currentData
        ? `Current test data JSON:\n${currentData}\n\nUser wants to: ${userPrompt}\n\nGenerate updated JSON with keys: ${varList}. Return ONLY valid JSON.`
        : `Generate realistic test data as JSON for variables: ${varList}.\n${instructions ? `Eval context: ${instructions.slice(0, 300)}` : ""}\nUser request: ${userPrompt}\nReturn ONLY valid JSON.`;

      const { data } = await axios.post(endpoints.develop.eval.aiEvalWriter, {
        description,
      });
      const raw = data?.result?.prompt;
      if (raw) {
        const match = raw.match(/\{[\s\S]*\}/);
        if (match) return match[0];
      }
      return null;
    },
    [variables, jsonText, instructions],
  );

  // Submit prompt
  const handleSubmit = useCallback(
    async (prompt) => {
      if (!prompt?.trim()) return;
      setAiLoading(true);
      if (originalJson === null) setOriginalJson(jsonText);

      try {
        const result = await callAI(prompt.trim());
        if (result) {
          const parsed = JSON.parse(result);
          const formatted = JSON.stringify(parsed, null, 2);
          handleJsonChange(formatted);
          setHasResult(true);
          setAiPrompt(prompt.trim());
          setTimeout(() => followUpRef.current?.focus(), 100);
        }
      } catch {
        // silent
      } finally {
        setAiLoading(false);
      }
    },
    [jsonText, originalJson, callAI, handleJsonChange],
  );

  const handleAccept = useCallback(() => {
    setAiOpen(false);
    setHasResult(false);
    setOriginalJson(null);
    setAiPrompt("");
  }, []);

  const handleReject = useCallback(() => {
    if (originalJson !== null) handleJsonChange(originalJson);
    setHasResult(false);
    setOriginalJson(null);
    setAiPrompt("");
  }, [originalJson, handleJsonChange]);

  const handleClose = useCallback(() => {
    if (hasResult && originalJson !== null) handleJsonChange(originalJson);
    setAiOpen(false);
    setHasResult(false);
    setOriginalJson(null);
    setAiPrompt("");
  }, [hasResult, originalJson, handleJsonChange]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <Typography variant="body2" fontWeight={600}>
        Test Data
      </Typography>

      <Box sx={{ position: "relative" }}>
        {/* ── AI bar (same as InstructionEditor) ── */}
        {aiOpen && (
          <Box
            sx={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              zIndex: 10,
              borderBottom: "1px solid",
              borderColor: "divider",
              backgroundColor: (theme) =>
                theme.palette.mode === "dark"
                  ? "rgba(26,26,46,0.95)"
                  : "rgba(250,250,254,0.95)",
              borderRadius: "8px 8px 0 0",
            }}
          >
            <Box sx={{ display: "flex", alignItems: "center", px: 1.5, pt: 1 }}>
              {aiLoading ? (
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    flex: 1,
                  }}
                >
                  <CircularProgress size={14} />
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ fontSize: "13px" }}
                  >
                    Generating...
                  </Typography>
                </Box>
              ) : !hasResult ? (
                <Box
                  component="input"
                  autoFocus
                  placeholder="Describe the test data you need — e.g. 'generate a failing case'"
                  value={aiPrompt}
                  onChange={(e) => setAiPrompt(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleSubmit(aiPrompt);
                    }
                    if (e.key === "Escape") handleClose();
                  }}
                  sx={{
                    flex: 1,
                    border: "none",
                    outline: "none",
                    fontSize: "13px",
                    backgroundColor: "transparent",
                    color: "text.primary",
                    "&::placeholder": { color: "text.disabled" },
                  }}
                />
              ) : (
                <Typography
                  variant="body2"
                  sx={{
                    flex: 1,
                    fontSize: "13px",
                    color: "text.secondary",
                    fontStyle: "italic",
                  }}
                >
                  {aiPrompt}
                </Typography>
              )}
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 0.5,
                  ml: 1,
                  flexShrink: 0,
                }}
              >
                {hasResult && (
                  <>
                    <Box
                      component="button"
                      onClick={handleReject}
                      sx={{
                        border: "none",
                        background: "none",
                        cursor: "pointer",
                        fontSize: "12px",
                        color: "text.secondary",
                        p: "4px 8px",
                      }}
                    >
                      Reject
                    </Box>
                    <Box
                      component="button"
                      onClick={handleAccept}
                      sx={{
                        border: "1px solid",
                        borderColor: "primary.main",
                        background: "none",
                        cursor: "pointer",
                        fontSize: "12px",
                        color: "primary.main",
                        fontWeight: 600,
                        p: "4px 10px",
                        borderRadius: "4px",
                      }}
                    >
                      Accept
                    </Box>
                  </>
                )}
                {!hasResult && !aiLoading && (
                  <IconButton
                    size="small"
                    onClick={() => handleSubmit(aiPrompt)}
                    disabled={!aiPrompt.trim()}
                    sx={{ p: 0.5 }}
                  >
                    <SvgColor
                      src="/assets/icons/navbar/ic_falcon_ai.svg"
                      sx={{
                        width: 16,
                        height: 16,
                        color: aiPrompt.trim()
                          ? "primary.main"
                          : "text.disabled",
                      }}
                    />
                  </IconButton>
                )}
                <IconButton size="small" onClick={handleClose} sx={{ p: 0.25 }}>
                  <Box
                    sx={{
                      fontSize: "14px",
                      color: "text.disabled",
                      lineHeight: 1,
                    }}
                  >
                    ✕
                  </Box>
                </IconButton>
              </Box>
            </Box>
            {hasResult && (
              <Box sx={{ px: 1.5, pb: 1, pt: 0.5 }}>
                <Box
                  component="input"
                  ref={followUpRef}
                  placeholder="Add a follow-up..."
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && e.target.value.trim()) {
                      e.preventDefault();
                      handleSubmit(e.target.value);
                      e.target.value = "";
                    }
                    if (e.key === "Escape") handleClose();
                  }}
                  sx={{
                    width: "100%",
                    border: "none",
                    outline: "none",
                    fontSize: "13px",
                    backgroundColor: "transparent",
                    color: "text.primary",
                    borderTop: "1px solid",
                    borderColor: "divider",
                    pt: 0.75,
                    "&::placeholder": { color: "text.disabled" },
                  }}
                />
              </Box>
            )}
          </Box>
        )}

        {/* ── Monaco JSON Editor ── */}
        <CodeEditor
          value={jsonText}
          onChange={(val) => handleJsonChange(val || "")}
          language="json"
          height="220px"
          placeholder={`{\n  "${variables[0] || "variable"}": "value"\n}`}
        />

        {/* ── Falcon button overlay (bottom-right) ── */}
        {!aiOpen && (
          <Box sx={{ position: "absolute", bottom: 8, right: 12, zIndex: 5 }}>
            <Tooltip title="Generate test data with Falcon AI" arrow>
              <IconButton
                size="small"
                onClick={() => setAiOpen(true)}
                sx={{
                  width: 30,
                  height: 30,
                  backgroundColor: (theme) =>
                    theme.palette.mode === "dark"
                      ? "rgba(20,26,33,0.9)"
                      : "rgba(250,251,252,0.9)",
                  border: "1px solid",
                  borderColor: "divider",
                  "&:hover": {
                    backgroundColor: (theme) =>
                      theme.palette.mode === "dark"
                        ? "rgba(124,77,255,0.15)"
                        : "rgba(124,77,255,0.08)",
                  },
                }}
              >
                <SvgColor
                  src="/assets/icons/navbar/ic_falcon_ai.svg"
                  sx={{ width: 18, height: 18, color: "primary.main" }}
                />
              </IconButton>
            </Tooltip>
          </Box>
        )}
      </Box>

      {jsonError && (
        <Typography
          variant="caption"
          color="error.main"
          sx={{ fontSize: "11px" }}
        >
          {jsonError}
        </Typography>
      )}

      {/* Variable mapping with dropdowns — same UI as DatasetTestMode */}
      {variables.length > 0 && (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
          {variables.map((variable) => (
            <Box
              key={variable}
              sx={{ display: "flex", alignItems: "center", gap: 1 }}
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 0.75,
                  px: 1.5,
                  py: 0.5,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "6px",
                  minWidth: 120,
                }}
              >
                <Box
                  sx={{
                    fontSize: "13px",
                    color: "text.secondary",
                    fontFamily: "monospace",
                  }}
                >
                  {"{}"}
                </Box>
                <Typography variant="caption" fontWeight={500}>
                  {variable}
                </Typography>
              </Box>
              <Box sx={{ fontSize: "14px", color: "text.disabled" }}>→</Box>
              <Select
                size="small"
                displayEmpty
                value={varMapping[variable] || ""}
                onChange={(e) => {
                  const selected = e.target.value;
                  setVarMapping((prev) => ({ ...prev, [variable]: selected }));
                  try {
                    const parsed = JSON.parse(jsonText);
                    const val = selected
                      .split(".")
                      .reduce((obj, k) => obj?.[k], parsed);
                    onInputChange(
                      variable,
                      typeof val === "object"
                        ? JSON.stringify(val)
                        : String(val ?? ""),
                    );
                  } catch {
                    /* ignore */
                  }
                }}
                sx={{ flex: 1, fontSize: "12px", height: 30 }}
              >
                <MenuItem value="" disabled sx={{ fontSize: "12px" }}>
                  Select column
                </MenuItem>
                {jsonKeys.map((k, i) => (
                  <MenuItem
                    key={`${k}-${i}`}
                    value={k}
                    sx={{
                      fontSize: "12px",
                      fontFamily: "monospace",
                      pl: k.includes(".")
                        ? `${12 + (k.split(".").length - 1) * 12}px`
                        : undefined,
                      color: k.includes(".") ? "primary.main" : "text.primary",
                    }}
                  >
                    {k}
                  </MenuItem>
                ))}
              </Select>
            </Box>
          ))}
        </Box>
      )}
    </Box>
  );
};

CustomJsonInput.propTypes = {
  variables: PropTypes.array.isRequired,
  inputValues: PropTypes.object.isRequired,
  onInputChange: PropTypes.func.isRequired,
  instructions: PropTypes.string,
  onColumnsLoaded: PropTypes.func,
};

const TestPlayground = React.forwardRef(
  (
    {
      templateId,
      instructions = "",
      evalType,
      model = "turing_large",
      requiredKeys = [],
      showVersions,
      onTestResult,
      onColumnsLoaded,
      onVersionSelect,
      onSourceTabChange,
      onClearResult,
      errorLocalizerEnabled = false,
      isComposite = false,
      compositeAdhocConfig = null,
      templateFormat = "mustache",
      functionParamsSchema = null,
      configParamsDesc = null,
      onReadyChange,
    },
    ref,
  ) => {
    const [activeMainTab, setActiveMainTab] = useState("test"); // "test" or "versions"
    const [activeTab, setActiveTab] = useState("Custom");
    const { data: versionsData } = useEvalVersions(templateId);
    const setDefaultVersion = useSetDefaultVersion(templateId);
    const restoreVersion = useRestoreVersion(templateId);
    const { enqueueSnackbar } = useSnackbar();
    const [isRunning, setIsRunning] = useState(false);
    const [result, setResult] = useState(null);
    const [error, setError] = useState(null);
    const {
      exhaustionError,
      handleError: handleCreditError,
      handleUpgradeClick,
      handleDismiss: dismissCreditBanner,
    } = useCreditExhaustion({ feature: "eval_playground" });
    const datasetTestRef = useRef(null);
    const tracingTestRef = useRef(null);
    const simulationTestRef = useRef(null);
    const { state: errorLocalizerState, start: startErrorLocalizerPoll } =
      useErrorLocalizerPoll();

    // Version hover menu state
    const [versionMenuAnchor, setVersionMenuAnchor] = useState(null);
    const [hoveredVersionId, setHoveredVersionId] = useState(null);
    const [menuVersion, setMenuVersion] = useState(null);
    const [selectedVersionId, setSelectedVersionId] = useState(null);

    const handleVersionMenuOpen = useCallback((e, version) => {
      e.stopPropagation();
      setVersionMenuAnchor(e.currentTarget);
      setMenuVersion(version);
    }, []);

    const handleVersionMenuClose = useCallback(() => {
      setVersionMenuAnchor(null);
      setMenuVersion(null);
    }, []);

    const handleSetDefault = useCallback(async () => {
      if (!menuVersion) return;
      try {
        await setDefaultVersion.mutateAsync(menuVersion.id);
        enqueueSnackbar(`V${menuVersion.version_number} set as default`, {
          variant: "success",
        });
      } catch {
        enqueueSnackbar("Failed to set default version", { variant: "error" });
      }
      handleVersionMenuClose();
    }, [
      menuVersion,
      setDefaultVersion,
      enqueueSnackbar,
      handleVersionMenuClose,
    ]);

    const handleRestore = useCallback(async () => {
      if (!menuVersion) return;
      try {
        const restored = await restoreVersion.mutateAsync(menuVersion.id);
        enqueueSnackbar(
          `Restored V${menuVersion.version_number} as new V${restored?.version_number || ""}`,
          { variant: "success" },
        );
      } catch {
        enqueueSnackbar("Failed to restore version", { variant: "error" });
      }
      handleVersionMenuClose();
    }, [menuVersion, restoreVersion, enqueueSnackbar, handleVersionMenuClose]);

    const handleViewConfig = useCallback(
      (version) => {
        setSelectedVersionId(version.id);
        onVersionSelect?.(version);
        handleVersionMenuClose();
      },
      [onVersionSelect, handleVersionMenuClose],
    );

    const handleVersionClick = useCallback(
      (version) => {
        const isAlreadySelected = selectedVersionId === version.id;
        if (isAlreadySelected) {
          setSelectedVersionId(null);
          onVersionSelect?.(null); // deselect — go back to current config
        } else {
          setSelectedVersionId(version.id);
          onVersionSelect?.(version);
        }
      },
      [selectedVersionId, onVersionSelect],
    );

    // Keep a ref to templateId so imperative handle always has latest value
    const templateIdRef = useRef(templateId);
    useEffect(() => {
      templateIdRef.current = templateId;
    }, [templateId]);

    // Extract variables from instructions
    const variables = React.useMemo(() => {
      // Composite templates have no instructions of their own; the
      // parent (EvalDetailPage / EvalPicker) has already unioned the
      // children's required_keys into `requiredKeys`. Use that list
      // directly so each child's variable gets an input field here.
      if (isComposite) {
        return Array.isArray(requiredKeys) ? [...new Set(requiredKeys)] : [];
      }

      // For code evals: use explicit requiredKeys if provided (e.g. system evals define these),
      // otherwise fall back to the standard input/output/expected trio.
      let codeStdVars = [];
      if (evalType === "code") {
        codeStdVars =
          Array.isArray(requiredKeys) && requiredKeys.length > 0
            ? requiredKeys
            : ["input", "output", "expected"];
      }

      if (!instructions && evalType !== "code") return [...codeStdVars];

      let vars;
      if (templateFormat === "jinja") {
        vars = extractJinjaVariables(instructions || "");
      } else {
        const matches =
          (instructions || "").match(/\{\{\s*([^{}]+?)\s*\}\}/g) || [];
        vars = matches.map((m) => m.replace(/\{\{|\}\}/g, "").trim());
      }
      return [...new Set([...codeStdVars, ...vars])];
    }, [instructions, evalType, requiredKeys, isComposite, templateFormat]);

    // Custom input values
    const [inputValues, setInputValues] = useState({});
    const inputValuesRef = useRef(inputValues);
    useEffect(() => {
      inputValuesRef.current = inputValues;
    }, [inputValues]);

    const handleInputChange = useCallback((variable, value) => {
      setInputValues((prev) => ({ ...prev, [variable]: value }));
    }, []);

    // Schema-defined params for code evals (from function_params_schema)
    const [codeParams, setCodeParams] = useState({});
    const codeParamsRef = useRef(codeParams);
    useEffect(() => {
      codeParamsRef.current = codeParams;
    }, [codeParams]);

    const handleCodeParamChange = useCallback((key, value) => {
      setCodeParams((prev) => ({ ...prev, [key]: value }));
    }, []);

    const visibleFunctionParamEntries = React.useMemo(() => {
      if (!functionParamsSchema) return [];
      const variableSet = new Set(Array.isArray(variables) ? variables : []);
      return canonicalEntries(functionParamsSchema).filter(
        ([key]) => !variableSet.has(key),
      );
    }, [functionParamsSchema, variables]);

    // Per-tab readiness — Custom always allows running (empty strings are
    // a valid input for exploratory testing); Dataset/Tracing/Simulation
    // report up via their own onReadyChange callbacks because they require
    // a row / dataset selection.
    const [tabReady, setTabReady] = useState({
      Custom: true,
      Dataset: false,
      Tracing: false,
      Simulation: false,
    });

    const handleDatasetReady = useCallback((isReady) => {
      setTabReady((prev) =>
        prev.Dataset === !!isReady ? prev : { ...prev, Dataset: !!isReady },
      );
    }, []);
    const handleTracingReady = useCallback((isReady) => {
      setTabReady((prev) =>
        prev.Tracing === !!isReady ? prev : { ...prev, Tracing: !!isReady },
      );
    }, []);
    const handleSimulationReady = useCallback((isReady) => {
      setTabReady((prev) =>
        prev.Simulation === !!isReady
          ? prev
          : { ...prev, Simulation: !!isReady },
      );
    }, []);

    useEffect(() => {
      if (!onReadyChange) return;
      onReadyChange(!!tabReady[activeTab]);
    }, [activeTab, tabReady, onReadyChange]);

    const executeComposite = useExecuteCompositeEval();
    const executeCompositeAdhoc = useExecuteCompositeEvalAdhoc();

    const handleRunTest = useCallback(async () => {
      const tid = templateIdRef.current;
      // Adhoc composite (eval create page) doesn't need a saved templateId.
      if (!tid && !compositeAdhocConfig) {
        onTestResult?.(false, "No template ID — save the eval first");
        return;
      }

      setIsRunning(true);
      setResult(null);
      setError(null);

      try {
        const mapping = {};
        const currentInputs = inputValuesRef.current;

        // Composite evals use a dedicated execute endpoint that runs every
        // child and optionally aggregates their scores.
        // Composite parent templates have no declared required_keys — child
        // evals each declare their own. Pass every key the user typed in the
        // JSON input so the execute endpoint can route them to children.
        if (isComposite) {
          Object.entries(currentInputs).forEach(([k, v]) => {
            mapping[k] = v == null ? "" : String(v);
          });

          const compositeConfig = buildCompositeRuntimeConfig({
            codeParams: evalType === "code" ? codeParamsRef.current : {},
          });

          const compositeResult = compositeAdhocConfig
            ? await executeCompositeAdhoc.mutateAsync({
                ...compositeAdhocConfig,
                mapping,
                config: compositeConfig,
                error_localizer: errorLocalizerEnabled,
                input_data_types: {},
              })
            : await executeComposite.mutateAsync({
                templateId: tid,
                payload: {
                  mapping,
                  config: compositeConfig,
                  error_localizer: errorLocalizerEnabled,
                  input_data_types: {},
                },
              });
          // Normalize composite response into the shape expected by
          // EvalResultDisplay while preserving the full composite payload.
          const adapted = {
            output:
              compositeResult?.aggregation_enabled &&
              compositeResult?.aggregate_score != null
                ? compositeResult.aggregate_score
                : null,
            reason: compositeResult?.summary || "",
            compositeResult,
          };
          setResult(adapted);
          onTestResult?.(true, adapted);
          return;
        }

        // Single eval: mapping from inputValues (strings)
        variables.forEach((v) => {
          mapping[v] = currentInputs[v] || "";
        });

        const params = evalType === "code" ? { ...codeParamsRef.current } : {};

        const { data } = await axios.post(
          endpoints.develop.eval.evalPlayground,
          {
            template_id: tid,
            model,
            error_localizer: errorLocalizerEnabled,
            config: {
              mapping,
              ...(evalType === "code" ? { params } : {}),
            },
          },
        );

        if (data?.status) {
          setResult(data.result);
          onTestResult?.(true, data.result);
          // Kick off the async error-localization poll when enabled —
          // the eval playground returns before the localizer task
          // finishes, so we poll `/get-eval-logs` and merge the
          // resulting error_details into `result` via the EvalResultDisplay
          // read path below.
          if (errorLocalizerEnabled && data.result?.log_id) {
            startErrorLocalizerPoll(data.result.log_id);
          }
        } else {
          const errMsg = data?.result || "Evaluation failed";
          setError(errMsg);
          onTestResult?.(false, errMsg);
        }
      } catch (err) {
        // Route credit/usage-limit errors into the dedicated exhaustion
        // banner so users see an upgrade CTA instead of a generic red
        // "failed" box. Fall back to the friendly `result` field that the
        // axios interceptor flattens (backend `usage_limit_response` sets
        // result=reason). `err.response.data.result` is dead — the
        // interceptor rejects with a flat custom error.
        if (handleCreditError(err)) {
          onTestResult?.(false, err?.result || "Usage limit exceeded");
        } else {
          const errMsg =
            err?.result ||
            err?.detail ||
            err?.response?.data?.result ||
            err?.message ||
            "Failed to run evaluation";
          setError(errMsg);
          onTestResult?.(false, errMsg);
        }
      } finally {
        setIsRunning(false);
      }
    }, [
      variables,
      onTestResult,
      errorLocalizerEnabled,
      startErrorLocalizerPoll,
      isComposite,
      executeComposite,
      compositeAdhocConfig,
      executeCompositeAdhoc,
      handleCreditError,
    ]);

    // Expose runTest and switchToVersion to parent via ref
    useImperativeHandle(
      ref,
      () => ({
        runTest: (overrideTemplateId) => {
          if (overrideTemplateId) {
            templateIdRef.current = overrideTemplateId;
          }
          const tid = overrideTemplateId || templateIdRef.current;
          if (activeTab === "Dataset" && datasetTestRef.current?.runTest) {
            datasetTestRef.current.runTest(tid);
          } else if (
            activeTab === "Tracing" &&
            tracingTestRef.current?.runTest
          ) {
            tracingTestRef.current.runTest(tid);
          } else if (
            activeTab === "Simulation" &&
            simulationTestRef.current?.runTest
          ) {
            simulationTestRef.current.runTest(tid);
          } else if (activeTab === "Custom") {
            handleRunTest();
          }
        },
        switchToVersion: (versionId) => {
          setActiveMainTab("versions");
          if (versionId) setSelectedVersionId(versionId);
        },
        isRunning,
      }),
      [activeTab, handleRunTest, isRunning],
    );

    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          height: "100%",
        }}
      >
        {/* Header — Test Evaluations + Versions (only if saved) */}
        <Box sx={{ display: "flex", gap: 2, mb: 1.5 }}>
          <Box
            onClick={() => setActiveMainTab("test")}
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.75,
              px: 1,
              py: 0.5,
              cursor: "pointer",
              borderBottom:
                activeMainTab === "test"
                  ? "2px solid"
                  : "2px solid transparent",
              borderColor:
                activeMainTab === "test" ? "primary.main" : "transparent",
            }}
          >
            <Iconify
              icon="solar:play-bold"
              width={14}
              sx={{
                color:
                  activeMainTab === "test" ? "primary.main" : "text.secondary",
              }}
            />
            <Typography
              variant="body2"
              sx={{
                fontSize: "12px",
                fontWeight: activeMainTab === "test" ? 500 : 400,
                color:
                  activeMainTab === "test" ? "primary.main" : "text.secondary",
              }}
            >
              Test Evaluation
            </Typography>
          </Box>
          {(templateId || showVersions) && (
            <Box
              onClick={() => setActiveMainTab("versions")}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.75,
                px: 1,
                py: 0.5,
                cursor: "pointer",
                borderBottom:
                  activeMainTab === "versions"
                    ? "2px solid"
                    : "2px solid transparent",
                borderColor:
                  activeMainTab === "versions" ? "primary.main" : "transparent",
              }}
            >
              <Iconify
                icon="iconamoon:history"
                width={14}
                sx={{
                  color:
                    activeMainTab === "versions"
                      ? "primary.main"
                      : "text.secondary",
                }}
              />
              <Typography
                variant="body2"
                sx={{
                  fontSize: "12px",
                  fontWeight: activeMainTab === "versions" ? 500 : 400,
                  color:
                    activeMainTab === "versions"
                      ? "primary.main"
                      : "text.secondary",
                }}
              >
                Versions
              </Typography>
            </Box>
          )}
        </Box>

        {activeMainTab === "test" ? (
          <>
            {/* Source tabs + Map Variables — same row */}
            <Box sx={{ display: "flex", alignItems: "center", mb: 2 }}>
              <Tabs
                value={activeTab}
                onChange={(_, val) => {
                  setActiveTab(val);
                  setError(null);
                  setResult(null);
                  setInputValues({});
                  onSourceTabChange?.();
                }}
                TabIndicatorProps={{ style: { display: "none" } }}
                sx={{
                  minHeight: 28,
                  "& .MuiTab-root": {
                    minHeight: 28,
                    px: 1.5,
                    py: 0,
                    mr: "0px !important",
                    textTransform: "none",
                    fontSize: "12px",
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
                {SOURCE_TABS.map((tab) => (
                  <Tab
                    key={tab}
                    value={tab}
                    label={tab}
                    sx={{
                      bgcolor:
                        activeTab === tab
                          ? (theme) =>
                              theme.palette.mode === "dark"
                                ? "rgba(255,255,255,0.12)"
                                : "background.paper"
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
                      color:
                        activeTab === tab ? "text.primary" : "text.disabled",
                    }}
                  />
                ))}
              </Tabs>
            </Box>

            {/* Tab content */}
            <Box sx={{ flex: 1, overflow: "auto" }}>
              {activeTab === "Custom" && (
                <>
                  <CustomJsonInput
                    variables={variables}
                    inputValues={inputValues}
                    onInputChange={handleInputChange}
                    instructions={instructions}
                    onColumnsLoaded={onColumnsLoaded}
                  />

                  {/* Result display */}
                  {result && (
                    <Box sx={{ mt: 2 }}>
                      <EvalResultDisplay
                        result={{
                          ...result,
                          ...(errorLocalizerState.status
                            ? {
                                error_localizer_status:
                                  errorLocalizerState.status,
                              }
                            : {}),
                          ...(errorLocalizerState.details
                            ? {
                                error_details:
                                  errorLocalizerState.details.error_analysis ||
                                  errorLocalizerState.details,
                                selected_input_key:
                                  errorLocalizerState.details
                                    .selected_input_key,
                                input_data:
                                  errorLocalizerState.details.input_data,
                                input_types:
                                  errorLocalizerState.details.input_types,
                              }
                            : {}),
                        }}
                      />
                    </Box>
                  )}

                  {/* Credit-limit / upgrade banner — takes precedence over
                      the generic error box so users see a clear CTA. */}
                  {exhaustionError && (
                    <Box sx={{ mt: 1 }}>
                      <CreditExhaustionBanner
                        error={exhaustionError}
                        onUpgrade={handleUpgradeClick}
                        onDismiss={dismissCreditBanner}
                      />
                    </Box>
                  )}

                  {/* Error */}
                  {error && !exhaustionError && (
                    <Box
                      sx={{
                        mt: 1,
                        p: 1.5,
                        borderRadius: "6px",
                        border: "1px solid",
                        borderColor: "error.main",
                        backgroundColor: "error.lighter",
                      }}
                    >
                      <Typography variant="caption" color="error.main">
                        {typeof error === "string"
                          ? error
                          : JSON.stringify(error)}
                      </Typography>
                    </Box>
                  )}
                </>
              )}
              {activeTab === "CustomOLD" && (
                <Box>
                  {/* placeholder to keep JSX structure — not rendered */}

                  {/* Result display */}
                  {result && (
                    <Box
                      sx={{
                        mt: 1,
                        p: 1.5,
                        borderRadius: 1,
                        border: "1px solid",
                        borderColor: "divider",
                        backgroundColor: "background.default",
                      }}
                    >
                      <Typography
                        variant="caption"
                        fontWeight={600}
                        sx={{ mb: 1, display: "block" }}
                      >
                        Result
                      </Typography>
                      {result.output && (
                        <Box sx={{ display: "flex", gap: 1, mb: 1 }}>
                          <Typography variant="caption" color="text.secondary">
                            Score:
                          </Typography>
                          <Chip
                            label={
                              result.output === "Passed"
                                ? "Pass"
                                : result.output
                            }
                            size="small"
                            color={
                              result.output === "Passed"
                                ? "success"
                                : result.output === "Failed"
                                  ? "error"
                                  : "default"
                            }
                            sx={{ fontSize: "11px", height: "20px" }}
                          />
                        </Box>
                      )}
                      {result.reason && (
                        <Box>
                          <Typography variant="caption" color="text.secondary">
                            Explanation:
                          </Typography>
                          <Box
                            component="pre"
                            sx={{
                              mt: 0.5,
                              m: 0,
                              p: 1.5,
                              fontFamily: "monospace",
                              fontSize: "12px",
                              lineHeight: 1.5,
                              whiteSpace: "pre-wrap",
                              wordBreak: "break-all",
                              color: "text.primary",
                              borderRadius: "6px",
                              border: "1px solid",
                              borderColor: "divider",
                              backgroundColor: (theme) =>
                                theme.palette.mode === "dark"
                                  ? "rgba(255,255,255,0.03)"
                                  : "background.neutral",
                            }}
                          >
                            {typeof result.reason === "string"
                              ? result.reason
                              : JSON.stringify(result.reason, null, 2)}
                          </Box>
                        </Box>
                      )}
                    </Box>
                  )}

                  {error && (
                    <Box
                      sx={{
                        mt: 1,
                        p: 1.5,
                        borderRadius: 1,
                        border: "1px solid",
                        borderColor: "error.main",
                        backgroundColor: "error.lighter",
                      }}
                    >
                      <Typography variant="caption" color="error.main">
                        {typeof error === "string"
                          ? error
                          : JSON.stringify(error)}
                      </Typography>
                    </Box>
                  )}
                </Box>
              )}

              {activeTab === "Dataset" && (
                <DatasetTestMode
                  ref={datasetTestRef}
                  templateId={templateId}
                  model={model}
                  variables={variables}
                  codeParams={codeParams}
                  onTestResult={onTestResult}
                  onColumnsLoaded={onColumnsLoaded}
                  onClearResult={onClearResult}
                  errorLocalizerEnabled={errorLocalizerEnabled}
                  onReadyChange={handleDatasetReady}
                  isComposite={isComposite}
                  compositeAdhocConfig={compositeAdhocConfig}
                />
              )}

              {activeTab === "Tracing" && (
                <TracingTestMode
                  ref={tracingTestRef}
                  templateId={templateId}
                  model={model}
                  variables={variables}
                  codeParams={codeParams}
                  onTestResult={onTestResult}
                  onColumnsLoaded={onColumnsLoaded}
                  onClearResult={onClearResult}
                  errorLocalizerEnabled={errorLocalizerEnabled}
                  onReadyChange={handleTracingReady}
                  isComposite={isComposite}
                  compositeAdhocConfig={compositeAdhocConfig}
                />
              )}

              {activeTab === "Simulation" && (
                <SimulationTestMode
                  ref={simulationTestRef}
                  templateId={templateId}
                  model={model}
                  variables={variables}
                  codeParams={codeParams}
                  onTestResult={onTestResult}
                  onColumnsLoaded={onColumnsLoaded}
                  onClearResult={onClearResult}
                  errorLocalizerEnabled={errorLocalizerEnabled}
                  onReadyChange={handleSimulationReady}
                  isComposite={isComposite}
                  compositeAdhocConfig={compositeAdhocConfig}
                />
              )}

              {/* Code eval params — visible on all source tabs */}
              {evalType === "code" &&
                visibleFunctionParamEntries.length > 0 && (
                  <Box sx={{ mt: 2 }}>
                    <Typography
                      variant="body2"
                      fontWeight={600}
                      sx={{ mb: 1 }}
                    >
                      Parameters
                    </Typography>
                    {visibleFunctionParamEntries.map(
                      ([key, schema]) => (
                        <Box
                          key={key}
                          sx={{
                            display: "flex",
                            alignItems: "center",
                            gap: 1,
                            mb: 0.75,
                          }}
                        >
                          <Tooltip
                            title={
                              configParamsDesc?.[key] ||
                              schema?.description ||
                              ""
                            }
                            placement="top"
                          >
                            <Typography
                              variant="caption"
                              sx={{
                                minWidth: 90,
                                fontFamily: "monospace",
                                color: "primary.main",
                              }}
                            >
                              {formatParamLabel(key)}
                            </Typography>
                          </Tooltip>
                          <Box
                            component="input"
                            type={
                              schema?.type === "integer" ||
                              schema?.type === "number"
                                ? "number"
                                : "text"
                            }
                            placeholder={
                              schema?.nullable
                                ? "optional"
                                : String(schema?.default ?? "")
                            }
                            value={codeParams[key] ?? ""}
                            onChange={(e) =>
                              handleCodeParamChange(key, e.target.value)
                            }
                            sx={{
                              flex: 1,
                              px: 1,
                              py: 0.5,
                              fontSize: "12px",
                              fontFamily: "monospace",
                              border: "1px solid",
                              borderColor: "divider",
                              borderRadius: "6px",
                              bgcolor: "background.paper",
                              color: "text.primary",
                              outline: "none",
                              "&:focus": { borderColor: "primary.main" },
                            }}
                          />
                        </Box>
                      ),
                    )}
                  </Box>
                )}
            </Box>
          </>
        ) : (
          /* =================== VERSIONS TAB =================== */
          <Box sx={{ flex: 1, overflow: "auto" }}>
            {!templateId ? (
              <Typography
                variant="body2"
                color="text.disabled"
                sx={{ mt: 4, textAlign: "center" }}
              >
                Save the evaluation first to create versions.
              </Typography>
            ) : !versionsData?.versions?.length ? (
              <Typography
                variant="body2"
                color="text.disabled"
                sx={{ mt: 4, textAlign: "center" }}
              >
                No versions yet. Click &ldquo;Save Version&rdquo; to create one.
              </Typography>
            ) : (
              /* Timeline-style version list */
              <>
                <Box sx={{ pt: 0.5 }}>
                  {versionsData.versions.map((v, idx) => {
                    const isDefault = v.is_default;
                    const isLast = idx === versionsData.versions.length - 1;
                    const vNum =
                      v.version_number || versionsData.versions.length - idx;
                    const dateStr = v.created_at
                      ? new Date(v.created_at).toLocaleString(undefined, {
                          month: "2-digit",
                          day: "2-digit",
                          year: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        })
                      : "";
                    const updatedBy =
                      v.created_by_name || v.created_by_email || "";
                    const isSelected = selectedVersionId === v.id;
                    const isHovered = hoveredVersionId === v.id;

                    return (
                      <Box
                        key={v.id}
                        sx={{
                          display: "flex",
                          alignItems: "flex-start",
                          gap: 1.5,
                          cursor: "pointer",
                          px: 1.5,
                          py: 1,
                          mx: -0.5,
                          borderRadius: "8px",
                          border: "1px solid",
                          borderColor: isSelected
                            ? "primary.main"
                            : "transparent",
                          backgroundColor: isSelected
                            ? (theme) =>
                                theme.palette.mode === "dark"
                                  ? "rgba(124,77,255,0.08)"
                                  : "rgba(124,77,255,0.04)"
                            : isHovered
                              ? (theme) =>
                                  theme.palette.mode === "dark"
                                    ? "rgba(255,255,255,0.05)"
                                    : "rgba(0,0,0,0.025)"
                              : "transparent",
                          transition: "all 0.15s",
                          "&:hover": {
                            backgroundColor: (theme) =>
                              isSelected
                                ? theme.palette.mode === "dark"
                                  ? "rgba(124,77,255,0.12)"
                                  : "rgba(124,77,255,0.06)"
                                : theme.palette.mode === "dark"
                                  ? "rgba(255,255,255,0.05)"
                                  : "rgba(0,0,0,0.025)",
                          },
                          mb: isLast ? 0 : 0.25,
                        }}
                        onClick={() => handleVersionClick(v)}
                        onMouseEnter={() => setHoveredVersionId(v.id)}
                        onMouseLeave={() => setHoveredVersionId(null)}
                      >
                        {/* Version badge + connecting line */}
                        <Box
                          sx={{
                            display: "flex",
                            flexDirection: "column",
                            alignItems: "center",
                            width: 28,
                            flexShrink: 0,
                            pt: 0.25,
                          }}
                        >
                          <Box
                            sx={{
                              width: 26,
                              height: 26,
                              borderRadius: "50%",
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "center",
                              backgroundColor: isSelected
                                ? "primary.main"
                                : isDefault
                                  ? "rgba(52,138,239,0.12)"
                                  : (theme) =>
                                      theme.palette.mode === "dark"
                                        ? "rgba(255,255,255,0.08)"
                                        : "action.hover",
                              border:
                                isDefault && !isSelected
                                  ? "1.5px solid rgba(52,138,239,0.3)"
                                  : "none",
                              flexShrink: 0,
                            }}
                          >
                            <Typography
                              sx={{
                                fontSize: "11px",
                                fontWeight: 600,
                                fontFamily: "'IBM Plex Sans', sans-serif",
                                color: isSelected
                                  ? "common.white"
                                  : isDefault
                                    ? "info.main"
                                    : "text.primary",
                              }}
                            >
                              V{vNum}
                            </Typography>
                          </Box>
                          {!isLast && (
                            <Box
                              sx={{
                                width: 1.5,
                                flex: 1,
                                minHeight: 12,
                                backgroundColor: "divider",
                                borderRadius: 1,
                                mt: 0.5,
                              }}
                            />
                          )}
                        </Box>

                        {/* Content */}
                        <Box sx={{ flex: 1, minWidth: 0 }}>
                          <Box
                            sx={{
                              display: "flex",
                              alignItems: "center",
                              gap: 0.75,
                            }}
                          >
                            <Typography
                              sx={{
                                fontSize: "12px",
                                fontWeight: 500,
                                fontFamily: "'IBM Plex Sans', sans-serif",
                                color: "text.primary",
                              }}
                            >
                              {dateStr}
                            </Typography>
                            {isDefault && (
                              <Box
                                sx={{
                                  px: 0.75,
                                  py: 0.125,
                                  borderRadius: "4px",
                                  fontSize: "10px",
                                  fontWeight: 600,
                                  fontFamily: "'IBM Plex Sans', sans-serif",
                                  backgroundColor: "rgba(0,162,81,0.1)",
                                  color: "success.main",
                                  lineHeight: 1.4,
                                }}
                              >
                                Default
                              </Box>
                            )}
                            {isSelected && !isDefault && (
                              <Box
                                sx={{
                                  px: 0.75,
                                  py: 0.125,
                                  borderRadius: "4px",
                                  fontSize: "10px",
                                  fontWeight: 600,
                                  fontFamily: "'IBM Plex Sans', sans-serif",
                                  backgroundColor: "rgba(124,77,255,0.1)",
                                  color: "primary.main",
                                  lineHeight: 1.4,
                                }}
                              >
                                Viewing
                              </Box>
                            )}
                          </Box>
                          {updatedBy && (
                            <Box
                              sx={{
                                display: "flex",
                                alignItems: "center",
                                gap: 0.5,
                                mt: 0.5,
                              }}
                            >
                              <Box
                                sx={{
                                  width: 16,
                                  height: 16,
                                  borderRadius: "50%",
                                  backgroundColor: (t) =>
                                    t.palette.mode === "dark"
                                      ? "rgba(124,77,255,0.15)"
                                      : "rgba(124,77,255,0.08)",
                                  display: "flex",
                                  alignItems: "center",
                                  justifyContent: "center",
                                  flexShrink: 0,
                                }}
                              >
                                <Typography
                                  sx={{
                                    fontSize: "9px",
                                    fontWeight: 600,
                                    color: "primary.main",
                                  }}
                                >
                                  {updatedBy.charAt(0).toUpperCase()}
                                </Typography>
                              </Box>
                              <Typography
                                sx={{
                                  fontSize: "11px",
                                  fontWeight: 400,
                                  fontFamily: "'IBM Plex Sans', sans-serif",
                                  color: "text.secondary",
                                }}
                                noWrap
                              >
                                {updatedBy}
                              </Typography>
                            </Box>
                          )}
                        </Box>

                        {/* Three-dot menu — always visible */}
                        <Tooltip title="Actions" arrow placement="left">
                          <IconButton
                            size="small"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleVersionMenuOpen(e, v);
                            }}
                            sx={{
                              width: 28,
                              height: 28,
                              flexShrink: 0,
                              mt: 0.25,
                              opacity: isHovered || versionMenuAnchor ? 1 : 0.4,
                              transition: "opacity 0.15s",
                              "&:hover": {
                                backgroundColor: (theme) =>
                                  theme.palette.mode === "dark"
                                    ? "rgba(255,255,255,0.1)"
                                    : "rgba(0,0,0,0.06)",
                              },
                            }}
                          >
                            <Iconify
                              icon="solar:menu-dots-bold"
                              width={16}
                              sx={{ color: "text.secondary" }}
                            />
                          </IconButton>
                        </Tooltip>
                      </Box>
                    );
                  })}
                </Box>

                {/* Version actions menu */}
                <Menu
                  anchorEl={versionMenuAnchor}
                  open={Boolean(versionMenuAnchor)}
                  onClose={handleVersionMenuClose}
                  anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
                  transformOrigin={{ vertical: "top", horizontal: "right" }}
                  slotProps={{
                    paper: {
                      sx: {
                        minWidth: 180,
                        boxShadow: 3,
                        borderRadius: "8px",
                        py: 0.5,
                      },
                    },
                  }}
                >
                  <MenuItem
                    onClick={() => {
                      handleViewConfig(menuVersion);
                    }}
                    sx={{ fontSize: "13px", gap: 1, py: 1 }}
                  >
                    <Iconify
                      icon="solar:eye-bold"
                      width={16}
                      sx={{ color: "text.secondary" }}
                    />
                    View Config
                  </MenuItem>
                  {!menuVersion?.is_default && (
                    <MenuItem
                      onClick={handleSetDefault}
                      sx={{ fontSize: "13px", gap: 1, py: 1 }}
                    >
                      <Iconify
                        icon="solar:star-bold"
                        width={16}
                        sx={{ color: "warning.main" }}
                      />
                      Set as Default
                    </MenuItem>
                  )}
                  <MenuItem
                    onClick={handleRestore}
                    sx={{ fontSize: "13px", gap: 1, py: 1 }}
                  >
                    <Iconify
                      icon="solar:restart-bold"
                      width={16}
                      sx={{ color: "info.main" }}
                    />
                    Restore Version
                  </MenuItem>
                </Menu>
              </>
            )}
          </Box>
        )}
      </Box>
    );
  },
);

TestPlayground.displayName = "TestPlayground";

TestPlayground.propTypes = {
  templateId: PropTypes.string,
  instructions: PropTypes.string,
  evalType: PropTypes.string,
  requiredKeys: PropTypes.array,
  showVersions: PropTypes.bool,
  onTestResult: PropTypes.func,
  onColumnsLoaded: PropTypes.func,
  onVersionSelect: PropTypes.func,
  errorLocalizerEnabled: PropTypes.bool,
  isComposite: PropTypes.bool,
  compositeAdhocConfig: PropTypes.object,
  templateFormat: PropTypes.string,
  onSourceTabChange: PropTypes.func,
  onClearResult: PropTypes.func,
  model: PropTypes.string,
  functionParamsSchema: PropTypes.object,
  configParamsDesc: PropTypes.object,
  onReadyChange: PropTypes.func,
};

export default TestPlayground;

/* eslint-disable react/prop-types */
import {
  Box,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  MenuItem,
  Popover,
  Skeleton,
  TextField,
  Tooltip,
  Typography,
  ButtonBase,
} from "@mui/material";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Iconify from "src/components/iconify";
import KeysDrawer from "src/components/custom-model-dropdown/KeysDrawer";
import { useDebounce } from "src/hooks/use-debounce";
import {
  useFeatureAllowed,
  useFeatureLocked,
  CAPABILITY,
} from "src/hooks/useCapabilities";
import { useNavigate } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { getProviderLogoFilterSx } from "./modelLogo";

// ---------------------------------------------------------------------------
// Modes — how the evaluator runs
// ---------------------------------------------------------------------------
const MODES = [
  {
    value: "auto",
    label: "Auto",
    description: "Balances quality and speed",
    icon: "solar:stars-minimalistic-linear",
  },
  {
    value: "agent",
    label: "Agent",
    description: "Deep, reasoning-based evaluation",
    icon: "solar:bot-linear",
  },
  {
    value: "quick",
    label: "Quick",
    description: "Runs fast, for quick iteration",
    icon: "solar:bolt-linear",
  },
];

// Connectors are fetched from the Falcon AI MCP connectors API

// ---------------------------------------------------------------------------
// Context injection options — what data to include when running evals
// ---------------------------------------------------------------------------
const CONTEXT_OPTIONS = [
  {
    value: "variables_only",
    label: "Template variables",
    desc: "Only mapped {{variables}} (default)",
    icon: "solar:code-linear",
    isDefault: true,
  },
  {
    value: "dataset_row",
    label: "Dataset row context",
    desc: "All columns from current row",
    icon: "solar:table-linear",
  },
  {
    value: "call_context",
    label: "Call context",
    desc: "Call transcript, recording, scenario",
    icon: "solar:phone-linear",
  },
  {
    value: "span_context",
    label: "Full span context",
    desc: "Complete span data + metadata",
    icon: "solar:layers-linear",
  },
  {
    value: "trace_context",
    label: "Trace context",
    desc: "Full trace tree with all spans",
    icon: "solar:folder-tree-linear",
  },
  {
    value: "session_context",
    label: "Session context",
    desc: "Full conversation history",
    icon: "solar:chat-round-line-linear",
  },
];

// ---------------------------------------------------------------------------
// Summary modes
// ---------------------------------------------------------------------------
const SUMMARY_OPTIONS = [
  {
    value: "short",
    label: "Short",
    description: "Brief summary with key points only.",
  },
  {
    value: "long",
    label: "Long",
    description: "Detailed summary with full context and explanations.",
  },
  {
    value: "concise",
    label: "Concise",
    description: "Compact summary focusing on essential insights.",
  },
  {
    value: "custom",
    label: "Custom",
    description: "Define your own summary criteria.",
  },
];

// ---------------------------------------------------------------------------
// FAGI built-in models — shown in a featured section at the top
// ---------------------------------------------------------------------------
const FAGI_MODELS = [
  {
    value: "turing_large",
    label: "Turing Large",
    description: "Best accuracy for complex evaluations",
    icon: "solar:star-bold",
  },
  {
    value: "turing_small",
    label: "Turing Small",
    description: "Balanced accuracy, lower cost",
    icon: "solar:star-2-linear",
  },
  {
    value: "turing_flash",
    label: "Turing Flash",
    description: "Fast, low-latency evaluations",
    icon: "solar:bolt-linear",
  },
];

export const FAGI_MODEL_VALUES = new Set(FAGI_MODELS.map((m) => m.value));

const FAGI_MODEL_LOCKED_TOOLTIP =
  "Turing models aren't enabled for this workspace. Select your own model.";

const CHIP_STYLES = {
  backgroundColor: "status.discovery.bg",
  "&:hover": {
    backgroundColor: "status.discovery.bg",
  },
  color: "status.discovery.fg",
  "& .MuiChip-icon": {
    color: "inherit",
  },
  "& .MuiChip-deleteIcon": {
    color: "status.discovery.fg",
    "&:hover": {
      color: "status.discovery.fg",
    },
  },
};

const DELETE_ICON = <Iconify icon="solar:close-linear" width={12} />;

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Summary Chip — resolves name for both presets and custom templates
// ---------------------------------------------------------------------------
function SummaryChip({ activeSummary, onClick, onDelete }) {
  // For custom templates, fetch the name
  const { data: savedTemplates = [] } = useQuery({
    queryKey: ["eval-summary-templates"],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.develop.eval.summaryTemplates);
      return data?.result?.templates || [];
    },
    enabled: activeSummary?.startsWith("custom:"),
  });

  let chipLabel;
  if (activeSummary?.startsWith("custom:")) {
    const templateId = activeSummary.replace("custom:", "");
    const tmpl = savedTemplates.find((t) => t.id === templateId);
    chipLabel = tmpl?.name || "Custom";
  } else {
    chipLabel =
      SUMMARY_OPTIONS.find((s) => s.value === activeSummary)?.label ||
      activeSummary;
  }

  return (
    <Chip
      size="small"
      label={chipLabel}
      onClick={onClick}
      onDelete={onDelete}
      deleteIcon={DELETE_ICON}
      sx={{ ...CHIP_STYLES, cursor: "pointer" }}
    />
  );
}

SummaryChip.propTypes = {
  activeSummary: PropTypes.string,
  onClick: PropTypes.func,
  onDelete: PropTypes.func,
};

// ---------------------------------------------------------------------------
// Summary Submenu — shows presets + saved custom templates with CRUD
// ---------------------------------------------------------------------------
function SummarySubmenu({ activeSummary, onSelect }) {
  const queryClient = useQueryClient();
  const [customMode, setCustomMode] = useState(false); // editing a custom template
  const [editId, setEditId] = useState(null);
  const [editName, setEditName] = useState("");
  const [editCriteria, setEditCriteria] = useState("");

  // Fetch saved custom templates
  const { data: savedTemplates = [] } = useQuery({
    queryKey: ["eval-summary-templates"],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.develop.eval.summaryTemplates);
      return data?.result?.templates || [];
    },
  });

  // Save / update
  const saveMutation = useMutation({
    mutationFn: async (payload) => {
      if (editId) {
        return axios.put(
          endpoints.develop.eval.summaryTemplate(editId),
          payload,
        );
      }
      return axios.post(endpoints.develop.eval.summaryTemplates, payload);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["eval-summary-templates"] });
      setCustomMode(false);
      setEditId(null);
      setEditName("");
      setEditCriteria("");
    },
  });

  // Delete
  const deleteMutation = useMutation({
    mutationFn: (id) =>
      axios.delete(endpoints.develop.eval.summaryTemplate(id)),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["eval-summary-templates"] }),
  });

  const handleSave = () => {
    if (!editName.trim() || !editCriteria.trim()) return;
    saveMutation.mutate({
      name: editName.trim(),
      criteria: editCriteria.trim(),
    });
  };

  const handleEdit = (tmpl) => {
    setEditId(tmpl.id);
    setEditName(tmpl.name);
    setEditCriteria(tmpl.criteria);
    setCustomMode(true);
  };

  if (customMode) {
    return (
      <Box sx={{ p: 1, minWidth: 250 }}>
        <Typography
          variant="caption"
          sx={{
            fontSize: "12px",
            fontWeight: 600,
            textTransform: "none",
            color: "text.disabled",
            mb: 1,
            display: "block",
          }}
        >
          {editId ? "Edit Template" : "New Custom Template"}
        </Typography>
        <TextField
          size="small"
          fullWidth
          autoFocus
          placeholder="Template name"
          value={editName}
          onChange={(e) => setEditName(e.target.value)}
          sx={{
            mb: 1,
            "& .MuiInputBase-root": { fontSize: "13px", height: 30 },
          }}
        />
        <TextField
          size="small"
          fullWidth
          multiline
          minRows={3}
          placeholder="Summary criteria — e.g. 'Provide a concise 2-3 sentence summary focusing on accuracy issues and suggested fixes'"
          value={editCriteria}
          onChange={(e) => setEditCriteria(e.target.value)}
          sx={{ mb: 1, "& .MuiInputBase-root": { fontSize: "13px" } }}
        />
        <Box sx={{ display: "flex", gap: 0.5, justifyContent: "flex-end" }}>
          <IconButton
            size="small"
            onClick={() => {
              setCustomMode(false);
              setEditId(null);
              setEditName("");
              setEditCriteria("");
            }}
          >
            <Iconify icon="solar:close-linear" width={16} />
          </IconButton>
          <IconButton
            size="small"
            onClick={handleSave}
            disabled={
              !editName.trim() || !editCriteria.trim() || saveMutation.isLoading
            }
            sx={{ color: "primary.main" }}
          >
            <Iconify icon="solar:check-linear" width={16} />
          </IconButton>
        </Box>
      </Box>
    );
  }

  return (
    <Box>
      {/* None / Disable option */}
      <MenuItem
        onClick={() => onSelect(null)}
        sx={{ borderRadius: "6px", py: 0.75, gap: 1 }}
      >
        <Iconify
          icon={!activeSummary ? "solar:check-linear" : "mdi:blank"}
          width={16}
          sx={{
            color: !activeSummary ? "primary.main" : "transparent",
            flexShrink: 0,
          }}
        />
        <Box>
          <Typography
            variant="body2"
            sx={{ fontSize: "13px", fontWeight: 500 }}
          >
            None
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ fontSize: "12px" }}
          >
            No summary — return raw evaluation output
          </Typography>
        </Box>
      </MenuItem>

      <Divider sx={{ my: 0.5 }} />

      {/* Built-in presets */}
      {SUMMARY_OPTIONS.filter((o) => o.value !== "custom").map((opt) => {
        const isSelected = activeSummary === opt.value;
        return (
          <MenuItem
            key={opt.value}
            onClick={() => onSelect(opt.value)}
            selected={isSelected}
            sx={{ borderRadius: "6px", py: 0.75, gap: 1 }}
          >
            <Iconify
              icon={isSelected ? "solar:check-linear" : "mdi:blank"}
              width={16}
              sx={{
                color: isSelected ? "primary.main" : "transparent",
                flexShrink: 0,
              }}
            />
            <Box>
              <Typography
                variant="body2"
                sx={{ fontSize: "13px", fontWeight: 500 }}
              >
                {opt.label}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontSize: "12px" }}
              >
                {opt.description}
              </Typography>
            </Box>
          </MenuItem>
        );
      })}

      {/* Saved custom templates */}
      {savedTemplates.length > 0 && (
        <>
          <Divider sx={{ my: 0.5 }} />
          <Typography
            variant="caption"
            sx={{
              px: 1.5,
              py: 0.5,
              display: "block",
              fontSize: "12px",
              fontWeight: 600,
              textTransform: "none",
              letterSpacing: "0.5px",
              color: "text.disabled",
            }}
          >
            Saved Templates
          </Typography>
          {savedTemplates.map((tmpl) => {
            const isSelected = activeSummary === `custom:${tmpl.id}`;
            return (
              <MenuItem
                key={tmpl.id}
                onClick={() => onSelect(`custom:${tmpl.id}`)}
                selected={isSelected}
                sx={{ borderRadius: "6px", py: 0.75, gap: 1 }}
              >
                <Iconify
                  icon={isSelected ? "solar:check-linear" : "mdi:blank"}
                  width={16}
                  sx={{
                    color: isSelected ? "primary.main" : "transparent",
                    flexShrink: 0,
                  }}
                />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography
                    variant="body2"
                    noWrap
                    sx={{ fontSize: "13px", fontWeight: 500 }}
                  >
                    {tmpl.name}
                  </Typography>
                  <Typography
                    variant="caption"
                    noWrap
                    color="text.secondary"
                    sx={{ fontSize: "12px" }}
                  >
                    {tmpl.criteria}
                  </Typography>
                </Box>
                <Box sx={{ display: "flex", gap: 0.25, flexShrink: 0 }}>
                  <IconButton
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleEdit(tmpl);
                    }}
                  >
                    <Iconify
                      icon="solar:pen-linear"
                      width={14}
                      sx={{ color: "text.disabled" }}
                    />
                  </IconButton>
                  <IconButton
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteMutation.mutate(tmpl.id);
                    }}
                  >
                    <Iconify
                      icon="solar:trash-bin-trash-linear"
                      width={14}
                      sx={{ color: "text.disabled" }}
                    />
                  </IconButton>
                </Box>
              </MenuItem>
            );
          })}
        </>
      )}

      {/* Create custom */}
      <Divider sx={{ my: 0.5 }} />
      <MenuItem
        onClick={() => setCustomMode(true)}
        sx={{ borderRadius: "6px", py: 0.75, gap: 1 }}
      >
        <Iconify
          icon="solar:add-linear"
          width={16}
          sx={{ color: "text.secondary" }}
        />
        <Typography
          variant="body2"
          sx={{ fontSize: "12px", color: "text.secondary" }}
        >
          Create custom template
        </Typography>
      </MenuItem>
    </Box>
  );
}

SummarySubmenu.propTypes = {
  activeSummary: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
};

function KBSkeletonRows({ count }) {
  return Array.from({ length: count }).map((_, i) => (
    <Box
      key={`kb-skeleton-${i}`}
      sx={{ display: "flex", alignItems: "center", gap: 1, px: 2, py: 0.75 }}
    >
      <Skeleton variant="rounded" width={18} height={18} />
      <Skeleton
        variant="text"
        sx={{ flex: 1, fontSize: (theme) => theme.typography.s2_1.fontSize }}
      />
    </Box>
  ));
}

// Main Component — [∞ Mode ▾] [model-name ▾] [+]
// ---------------------------------------------------------------------------
const ModelSelector = ({
  model,
  onModelChange,
  disabled = false,
  showMode = true,
  showPlus = true,
  // Optional controlled props — pass these to lift state up into the parent
  // so the picked mode / internet / summary / connectors / KBs can be saved
  // as runtime overrides when the user attaches the eval to a dataset. Falls
  // back to internal state when the parent doesn't pass them (backward compat).
  mode: modeProp,
  onModeChange,
  useInternet: useInternetProp,
  onUseInternetChange,
  activeSummary: activeSummaryProp,
  onActiveSummaryChange,
  activeConnectorIds: activeConnectorIdsProp,
  onActiveConnectorIdsChange,
  selectedKBs: selectedKBsProp,
  onSelectedKBsChange,
  activeContextOptions: activeContextOptionsProp,
  onActiveContextOptionsChange,
  hideDatasetContextToggle = false,
  // Bump this counter to pop the model menu open from the parent — used when
  // an action is blocked because no model is picked, so the snackbar and the
  // menu arrive together. A counter rather than a boolean so repeat attempts
  // re-open it without the parent having to reset the flag.
  openModelMenuSignal = 0,
}) => {
  // For each field, pick "controlled" (parent-driven) or "uncontrolled" (local state).
  const [modeLocal, setModeLocal] = useState("agent");
  const mode = modeProp !== undefined ? modeProp : modeLocal;
  const setMode = (v) => {
    if (onModeChange) onModeChange(v);
    else setModeLocal(v);
  };

  const [modeAnchor, setModeAnchor] = useState(null);
  const [modelAnchor, setModelAnchor] = useState(null);
  const modelPillRef = useRef(null);

  useEffect(() => {
    if (!openModelMenuSignal || disabled) return;
    setModelAnchor(modelPillRef.current);
  }, [openModelMenuSignal, disabled]);
  const [modelSearch, setModelSearch] = useState("");
  const debouncedModelSearch = useDebounce(modelSearch.trim(), 400);
  const [plusAnchor, setPlusAnchor] = useState(null);
  const [plusSubmenu, setPlusSubmenu] = useState(null);
  const [connectorSearch, setConnectorSearch] = useState("");

  const [activeConnectorIdsLocal, setActiveConnectorIdsLocal] = useState([]);
  const activeConnectorIds =
    activeConnectorIdsProp !== undefined
      ? activeConnectorIdsProp
      : activeConnectorIdsLocal;
  const setActiveConnectorIds = (updater) => {
    const next =
      typeof updater === "function" ? updater(activeConnectorIds) : updater;
    if (onActiveConnectorIdsChange) onActiveConnectorIdsChange(next);
    else setActiveConnectorIdsLocal(next);
  };

  const [activeSummaryLocal, setActiveSummaryLocal] = useState("concise");
  const activeSummary =
    activeSummaryProp !== undefined ? activeSummaryProp : activeSummaryLocal;
  const setActiveSummary = (v) => {
    if (onActiveSummaryChange) onActiveSummaryChange(v);
    else setActiveSummaryLocal(v);
  };

  const [selectedKBsLocal, setSelectedKBsLocal] = useState([]);
  const selectedKBs =
    selectedKBsProp !== undefined ? selectedKBsProp : selectedKBsLocal;
  const setSelectedKBs = (updater) => {
    const next = typeof updater === "function" ? updater(selectedKBs) : updater;
    if (onSelectedKBsChange) onSelectedKBsChange(next);
    else setSelectedKBsLocal(next);
  };

  const [useInternetLocal, setUseInternetLocal] = useState(false);
  const useInternet =
    useInternetProp !== undefined ? useInternetProp : useInternetLocal;
  const setUseInternet = (updater) => {
    const next = typeof updater === "function" ? updater(useInternet) : updater;
    if (onUseInternetChange) onUseInternetChange(next);
    else setUseInternetLocal(next);
  };

  const [activeContextOptionsLocal, setActiveContextOptionsLocal] = useState([
    "variables_only",
  ]);
  const activeContextOptions =
    activeContextOptionsProp !== undefined
      ? activeContextOptionsProp
      : activeContextOptionsLocal;
  const setActiveContextOptions = (updater) => {
    const next =
      typeof updater === "function" ? updater(activeContextOptions) : updater;
    if (onActiveContextOptionsChange) onActiveContextOptionsChange(next);
    else setActiveContextOptionsLocal(next);
  };

  const [kbSearch, setKbSearch] = useState("");
  const debouncedKbSearch = useDebounce(kbSearch.trim(), 400);
  const [keysDrawerModel, setKeysDrawerModel] = useState(null);
  const navigate = useNavigate();
  // Fail closed while capabilities load: never flash Turing models as
  // selectable during the fetch. `fagiModelsDenied` is the *confirmed* denial
  // (loaded AND not allowed) used only to clear a seeded selection below — we
  // must not wipe a legitimately-preselected Turing model mid-fetch.
  const { locked: fagiModelsLocked, isLoading: capabilitiesLoading } =
    useFeatureLocked(CAPABILITY.TURING_MODELS);
  const fagiModelsDenied = fagiModelsLocked && !capabilitiesLoading;
  const { allowed: falconAllowed } = useFeatureAllowed(CAPABILITY.FALCON_AI);

  const currentMode = MODES.find((m) => m.value === mode) || MODES[1];

  // Fetch real MCP connectors from Falcon AI API. Falcon AI needs a
  // license/plan; skip the call when locked so the 402 doesn't fire a
  // snackbar.
  const { data: connectorsData } = useInfiniteQuery({
    queryKey: ["falcon-mcp-connectors"],
    queryFn: () => axios.get(endpoints.falconAI.connectors),
    getNextPageParam: () => null,
    initialPageParam: 1,
    staleTime: 60000,
    enabled: falconAllowed,
  });

  const connectors = useMemo(() => {
    const results =
      connectorsData?.pages?.[0]?.data?.results ||
      connectorsData?.pages?.[0]?.data ||
      [];
    return Array.isArray(results) ? results : [];
  }, [connectorsData]);

  const {
    data: kbData,
    fetchNextPage: fetchNextKBPage,
    hasNextPage: hasNextKBPage,
    isFetchingNextPage: isFetchingNextKBPage,
    isLoading: kbLoading,
  } = useInfiniteQuery({
    queryKey: ["eval-knowledge-bases", debouncedKbSearch],
    queryFn: ({ pageParam }) =>
      axios.get(endpoints.knowledge.list, {
        params: {
          search: debouncedKbSearch,
          page_number: pageParam,
          page_size: 25,
        },
      }),
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce(
        (n, p) => n + (p?.data?.result?.table_data?.length || 0),
        0,
      );
      const total = lastPage?.data?.result?.total_rows || 0;
      return loaded < total ? allPages.length : undefined;
    },
    initialPageParam: 0,
    staleTime: 60000,
  });

  // API returns {status, result: {table_data: [...], total_rows}} per page.
  const knowledgeBases = useMemo(
    () =>
      (kbData?.pages || []).flatMap((p) => p?.data?.result?.table_data || []),
    [kbData],
  );

  // Fetch BYOK models from API
  const {
    data: modelPages,
    isLoading: modelsLoading,
    fetchNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["eval-model-list", debouncedModelSearch],
    queryFn: ({ pageParam }) =>
      axios.get(endpoints.develop.modelList, {
        params: {
          page: pageParam,
          search: debouncedModelSearch,
          model_type: "llm",
        },
      }),
    getNextPageParam: (o) => (o.data.next ? o.data.current_page + 1 : null),
    initialPageParam: 1,
    enabled: Boolean(modelAnchor),
  });

  const apiModels = useMemo(() => {
    const all =
      modelPages?.pages.reduce((acc, p) => [...acc, ...p.data.results], []) ||
      [];
    // Filter out FAGI models to avoid duplicates
    return all.filter((m) => !FAGI_MODEL_VALUES.has(m.model_name));
  }, [modelPages]);

  // Determine display name for the current model. Match on the full
  // canonical value first (e.g. "turing_small"); fall back to a suffix
  // match so a stray bare value ("small", "large", "flash") — which the
  // backend's eval-list endpoint sometimes returns for built-in templates
  // — still resolves to its FAGI label instead of rendering as "small".
  const modelDisplayName = useMemo(() => {
    if (!model) return "Select model";
    const exact = FAGI_MODELS.find((m) => m.value === model);
    if (exact) return exact.label;
    const suffix = FAGI_MODELS.find((m) => m.value.endsWith(`_${model}`));
    if (suffix) return suffix.label;
    return model;
  }, [model]);

  const handleToggleConnector = useCallback((id) => {
    setActiveConnectorIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  }, []);

  const filteredConnectors = useMemo(() => {
    if (!connectorSearch) return connectors;
    return connectors.filter((c) =>
      c.name?.toLowerCase().includes(connectorSearch.toLowerCase()),
    );
  }, [connectors, connectorSearch]);

  const filteredFagiModels = useMemo(() => {
    if (!modelSearch) return FAGI_MODELS;
    return FAGI_MODELS.filter((m) =>
      m.label.toLowerCase().includes(modelSearch.toLowerCase()),
    );
  }, [modelSearch]);

  // When Turing models aren't enabled, callers still seed `model` with
  // "turing_large". Drop it so the pill reads "Select model" instead of
  // preselecting something the backend would 402 on save. Gate on the
  // *confirmed* denial, not the loading-time lock, or we'd clear a legitimate
  // selection before capabilities resolve.
  useEffect(() => {
    if (fagiModelsDenied && FAGI_MODEL_VALUES.has(model)) onModelChange("");
  }, [fagiModelsDenied, model, onModelChange]);

  return (
    <Box
      sx={{ display: "flex", alignItems: "center", gap: 0.5, flexWrap: "wrap" }}
    >
      {/* ── Mode selector (left pill) — hidden in LLM-As-A-Judge ── */}
      {showMode && (
        <ButtonBase
          onClick={(e) => !disabled && setModeAnchor(e.currentTarget)}
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            pl: 1,
            pr: 0.75,
            py: 0.4,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "6px",
            cursor: disabled ? "default" : "pointer",
            backgroundColor: "action.hover",
            "&:hover": disabled ? {} : { borderColor: "text.secondary" },
            transition: "all 0.15s",
          }}
        >
          <Iconify
            icon={currentMode.icon}
            width={14}
            sx={{ color: "text.secondary" }}
          />
          <Typography
            variant="body2"
            sx={{ fontSize: "13px", fontWeight: 500 }}
          >
            {currentMode.label}
          </Typography>
          <Iconify
            icon="solar:alt-arrow-down-linear"
            width={14}
            sx={{ color: "text.disabled" }}
          />
        </ButtonBase>
      )}

      {/* ── Model selector (right) ── */}
      <ButtonBase
        ref={modelPillRef}
        onClick={(e) => !disabled && setModelAnchor(e.currentTarget)}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          px: 1,
          py: 0.4,
          cursor: disabled ? "default" : "pointer",
          "&:hover": disabled ? {} : { opacity: 0.7 },
          transition: "opacity 0.15s",
        }}
      >
        <Typography
          variant="body2"
          sx={{ fontSize: "13px", color: "text.secondary" }}
        >
          {modelDisplayName}
        </Typography>
        <Iconify
          icon="solar:alt-arrow-down-linear"
          width={14}
          sx={{ color: "text.disabled" }}
        />
      </ButtonBase>

      {/* ── + button + config chips (hidden when showPlus=false, e.g. LLM tab) ── */}
      {showPlus && (
        <IconButton
          size="small"
          disabled={disabled}
          onClick={(e) => setPlusAnchor(e.currentTarget)}
          sx={{
            border: "1px solid",
            borderColor: "divider",
          }}
        >
          <Iconify icon="solar:add-linear" width={14} />
        </IconButton>
      )}

      {/* ── Active config chips (only when + menu is available) ── */}
      {showPlus && useInternet && (
        <Chip
          size="small"
          icon={
            <Iconify icon="solar:global-linear" width={12} sx={{ ml: 0.5 }} />
          }
          label="Internet"
          onDelete={() => setUseInternet(false)}
          deleteIcon={DELETE_ICON}
          sx={CHIP_STYLES}
        />
      )}
      {showPlus && activeSummary && activeSummary !== "concise" && (
        <SummaryChip
          activeSummary={activeSummary}
          onClick={(e) => {
            setPlusAnchor(e.currentTarget);
            setPlusSubmenu("summary");
          }}
          onDelete={() => setActiveSummary("concise")}
        />
      )}
      {showPlus && !activeSummary && (
        <Chip
          size="small"
          label="No summary"
          onClick={(e) => {
            setPlusAnchor(e.currentTarget);
            setPlusSubmenu("summary");
          }}
          onDelete={() => setActiveSummary("concise")}
          variant="outlined"
          sx={{
            cursor: "pointer",
            color: "text.secondary",
          }}
        />
      )}
      {showPlus &&
        activeConnectorIds.map((cId) => {
          const conn = connectors.find((c) => c.id === cId);
          if (!conn) return null;
          return (
            <Chip
              key={cId}
              size="small"
              icon={
                <Box
                  sx={{
                    width: 7,
                    // eslint-disable-next-line no-restricted-syntax -- ds: status dot size
                    height: 7,
                    // eslint-disable-next-line no-restricted-syntax -- ds: status dot size
                    borderRadius: "50%",
                    ml: 0.5,
                    backgroundColor: conn.is_verified
                      ? "status.success.solid"
                      : "text.disabled",
                  }}
                />
              }
              label={conn.name}
              onDelete={() =>
                setActiveConnectorIds((p) => p.filter((x) => x !== cId))
              }
              deleteIcon={DELETE_ICON}
              sx={CHIP_STYLES}
            />
          );
        })}
      {showPlus && selectedKBs.length > 0 && (
        <Tooltip
          arrow
          title={
            <Box sx={{ py: 0.25 }}>
              <Typography sx={{ fontSize: "12px", fontWeight: 600, mb: 0.5 }}>
                Knowledge Bases
              </Typography>
              {selectedKBs.map((kbId) => {
                const kb = knowledgeBases.find((k) => k.id === kbId);
                return (
                  <Typography key={kbId} sx={{ fontSize: "12px" }}>
                    • {kb?.name || "KB"}
                  </Typography>
                );
              })}
            </Box>
          }
        >
          <Chip
            size="small"
            icon={
              <Iconify icon="solar:book-linear" width={12} sx={{ ml: 0.5 }} />
            }
            label={`${selectedKBs.length} KB`}
            onClick={(e) => {
              setPlusAnchor(e.currentTarget);
              setPlusSubmenu("knowledge");
            }}
            onDelete={() => setSelectedKBs([])}
            deleteIcon={DELETE_ICON}
            sx={{ ...CHIP_STYLES, cursor: "pointer" }}
          />
        </Tooltip>
      )}
      {showPlus &&
        activeContextOptions.filter((o) => o !== "variables_only").length >
          0 && (
          <Tooltip
            arrow
            title={
              <Box sx={{ py: 0.25 }}>
                <Typography sx={{ fontSize: "12px", fontWeight: 600, mb: 0.5 }}>
                  Data Injection
                </Typography>
                {activeContextOptions
                  .filter((o) => o !== "variables_only")
                  .map((optVal) => {
                    const opt = CONTEXT_OPTIONS.find((c) => c.value === optVal);
                    return (
                      <Typography key={optVal} sx={{ fontSize: "12px" }}>
                        • {opt?.label || optVal}
                      </Typography>
                    );
                  })}
              </Box>
            }
          >
            <Chip
              size="small"
              icon={
                <Iconify
                  icon="solar:layers-linear"
                  width={12}
                  sx={{ ml: 0.5 }}
                />
              }
              label={`+${activeContextOptions.filter((o) => o !== "variables_only").length} context`}
              onClick={(e) => {
                setPlusAnchor(e.currentTarget);
                setPlusSubmenu("injection");
              }}
              onDelete={() => setActiveContextOptions(["variables_only"])}
              deleteIcon={DELETE_ICON}
              sx={{ ...CHIP_STYLES, cursor: "pointer" }}
            />
          </Tooltip>
        )}

      {/* ══════ Mode Dropdown ══════ */}
      <Popover
        open={Boolean(modeAnchor)}
        anchorEl={modeAnchor}
        onClose={() => setModeAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: { sx: { minWidth: 240, borderRadius: "8px", p: 0.5 } },
        }}
      >
        {MODES.map((m) => (
          <MenuItem
            key={m.value}
            selected={m.value === mode}
            onClick={() => {
              setMode(m.value);
              setModeAnchor(null);
            }}
            sx={{ borderRadius: "6px", mx: 0.5, py: 0.75 }}
          >
            <Iconify
              icon={m.icon}
              width={20}
              sx={{
                mr: 1.5,
                color: m.value === mode ? "primary.main" : "text.secondary",
              }}
            />
            <Box sx={{ flex: 1 }}>
              <Typography
                variant="body2"
                sx={{ fontSize: "13px", fontWeight: 500 }}
              >
                {m.label}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontSize: "12px" }}
              >
                {m.description}
              </Typography>
            </Box>
            {m.value === mode && (
              <Iconify
                icon="solar:check-linear"
                width={16}
                sx={{ color: "primary.main", ml: 1 }}
              />
            )}
          </MenuItem>
        ))}
      </Popover>

      {/* ══════ Model Dropdown — two sections ══════ */}
      <Popover
        open={Boolean(modelAnchor)}
        anchorEl={modelAnchor}
        onClose={() => {
          setModelAnchor(null);
          setModelSearch("");
        }}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { width: 320, borderRadius: "8px", p: 0 } } }}
      >
        {/* Search */}
        <Box sx={{ p: 1, borderBottom: "1px solid", borderColor: "divider" }}>
          <TextField
            size="small"
            fullWidth
            autoFocus
            placeholder="Search models..."
            value={modelSearch}
            onChange={(e) => setModelSearch(e.target.value)}
            InputProps={{
              startAdornment: (
                <Iconify
                  icon="solar:magnifier-linear"
                  width={16}
                  sx={{ mr: 0.5, color: "text.disabled" }}
                />
              ),
              sx: { fontSize: "13px", height: 32 },
            }}
          />
        </Box>

        <Box
          sx={{ maxHeight: 350, overflowY: "auto" }}
          onScroll={(e) => {
            const { scrollTop, scrollHeight, clientHeight } = e.target;
            if (
              scrollHeight - scrollTop - clientHeight < 50 &&
              !isFetchingNextPage
            ) {
              fetchNextPage();
            }
          }}
        >
          {/* Section 1: FutureAGI Models */}
          {filteredFagiModels.length > 0 && (
            <>
              <Typography
                variant="caption"
                sx={{
                  px: 1.5,
                  pt: 1.5,
                  pb: 0.5,
                  display: "block",
                  fontSize: "12px",
                  fontWeight: 600,
                  textTransform: "none",
                  letterSpacing: "0.5px",
                  color: "primary.main",
                }}
              >
                FutureAGI Models
              </Typography>
              {filteredFagiModels.map((m) => (
                <Tooltip
                  key={m.value}
                  title={fagiModelsLocked ? FAGI_MODEL_LOCKED_TOOLTIP : ""}
                  placement="right"
                  arrow
                >
                  <span>
                    <MenuItem
                      selected={m.value === model}
                      disabled={fagiModelsLocked}
                      onClick={() => {
                        onModelChange(m.value);
                        setModelAnchor(null);
                        setModelSearch("");
                      }}
                      sx={{
                        mx: 0.5,
                        borderRadius: "6px",
                        py: 0.75,
                        gap: 1,
                        width: "auto",
                      }}
                    >
                      <Iconify
                        icon={m.icon}
                        width={20}
                        sx={{
                          color:
                            m.value === model
                              ? "primary.main"
                              : "text.secondary",
                          flexShrink: 0,
                        }}
                      />
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        <Typography
                          variant="body2"
                          sx={{ fontSize: "13px", fontWeight: 500 }}
                        >
                          {m.label}
                        </Typography>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ fontSize: "12px" }}
                        >
                          {fagiModelsLocked ? "Not enabled" : m.description}
                        </Typography>
                      </Box>
                      {fagiModelsLocked ? (
                        <Iconify
                          icon="solar:lock-linear"
                          width={16}
                          sx={{ color: "text.disabled", flexShrink: 0 }}
                        />
                      ) : (
                        m.value === model && (
                          <Iconify
                            icon="solar:check-linear"
                            width={16}
                            sx={{ color: "primary.main", flexShrink: 0 }}
                          />
                        )
                      )}
                    </MenuItem>
                  </span>
                </Tooltip>
              ))}
            </>
          )}

          {/* Divider between sections */}
          {filteredFagiModels.length > 0 && apiModels.length > 0 && (
            <Divider sx={{ my: 0.5 }} />
          )}

          {/* Section 2: Your LLM Models (BYOK) */}
          {(apiModels.length > 0 || modelsLoading) && (
            <>
              <Typography
                variant="caption"
                sx={{
                  px: 1.5,
                  pt: 1,
                  pb: 0.5,
                  display: "block",
                  fontSize: "12px",
                  fontWeight: 600,
                  textTransform: "none",
                  letterSpacing: "0.5px",
                  color: "text.disabled",
                }}
              >
                Your Models
              </Typography>
              {apiModels.map((m) => {
                const available = m.is_available !== false;
                const logoUrl = m.logo_url;
                return (
                  <MenuItem
                    key={m.model_name}
                    selected={m.model_name === model}
                    onClick={() => {
                      if (!available) {
                        // Open keys drawer for this model
                        setKeysDrawerModel(m);
                        return;
                      }
                      onModelChange(m.model_name);
                      setModelAnchor(null);
                      setModelSearch("");
                    }}
                    sx={{
                      mx: 0.5,
                      borderRadius: "6px",
                      py: 0.75,
                      gap: 1,
                      ...(!available && {
                        opacity: 0.8,
                        "&:hover": { backgroundColor: "status.danger.bg" },
                      }),
                    }}
                  >
                    {logoUrl ? (
                      <Box
                        component="img"
                        src={logoUrl}
                        sx={{
                          width: 18,
                          height: 18,
                          borderRadius: "4px",
                          flexShrink: 0,
                          ...getProviderLogoFilterSx(m.providers),
                        }}
                      />
                    ) : (
                      <Iconify
                        icon="solar:box-linear"
                        width={20}
                        sx={{
                          color: available
                            ? "text.secondary"
                            : "status.danger.fg",
                          flexShrink: 0,
                        }}
                      />
                    )}
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      <Typography
                        variant="body2"
                        noWrap
                        sx={{
                          fontSize: "13px",
                          fontWeight: 500,
                          color: available
                            ? "text.primary"
                            : "status.danger.fg",
                        }}
                      >
                        {m.model_name}
                      </Typography>
                      {m.providers && (
                        <Typography
                          variant="caption"
                          noWrap
                          sx={{
                            fontSize: "12px",
                            color: available
                              ? "text.secondary"
                              : "status.danger.fg",
                          }}
                        >
                          {m.providers}
                        </Typography>
                      )}
                    </Box>
                    {!available ? (
                      <Iconify
                        icon="solar:key-linear"
                        width={16}
                        sx={{ color: "status.danger.fg", flexShrink: 0 }}
                      />
                    ) : m.model_name === model ? (
                      <Iconify
                        icon="solar:check-linear"
                        width={16}
                        sx={{ color: "primary.main", flexShrink: 0 }}
                      />
                    ) : null}
                  </MenuItem>
                );
              })}
              {(modelsLoading || isFetchingNextPage) && (
                <Box
                  sx={{ display: "flex", justifyContent: "center", py: 1.5 }}
                >
                  <CircularProgress size={18} />
                </Box>
              )}
            </>
          )}

          {/* Empty state */}
          {!modelsLoading &&
            filteredFagiModels.length === 0 &&
            apiModels.length === 0 && (
              <Typography
                variant="body2"
                color="text.disabled"
                sx={{ py: 3, textAlign: "center", fontSize: "13px" }}
              >
                No models found
              </Typography>
            )}
        </Box>
      </Popover>

      {/* ══════ Plus Menu ══════ */}
      <Popover
        open={Boolean(plusAnchor)}
        anchorEl={plusAnchor}
        onClose={() => {
          setPlusAnchor(null);
          setPlusSubmenu(null);
          setConnectorSearch("");
        }}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: { sx: { borderRadius: "8px", p: 0.5, display: "flex" } },
        }}
      >
        {/* Main menu */}
        <Box sx={{ minWidth: 220 }}>
          {/* Internet toggle */}
          <MenuItem
            onClick={() => setUseInternet((prev) => !prev)}
            sx={{ borderRadius: "6px", py: 1 }}
          >
            <Iconify
              icon="solar:global-linear"
              width={16}
              sx={{
                mr: 1.5,
                color: useInternet ? "primary.main" : "text.secondary",
              }}
            />
            <Box sx={{ flex: 1 }}>
              <Typography
                variant="body2"
                sx={{ fontSize: "13px", fontWeight: 500 }}
              >
                Use Internet
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontSize: "12px" }}
              >
                Search the web during evaluation
              </Typography>
            </Box>
            <Box
              sx={{
                width: 32,
                height: 16,
                borderRadius: 8,
                position: "relative",
                backgroundColor: useInternet ? "primary.main" : "control.track",
                transition: "all 0.2s",
                flexShrink: 0,
              }}
            >
              <Box
                sx={{
                  width: 12,
                  height: 12,
                  borderRadius: "50%",
                  backgroundColor: useInternet
                    ? "primary.contrastText"
                    : "common.white",
                  position: "absolute",
                  top: 2,
                  left: useInternet ? 18 : 2,
                  transition: "left 0.2s",
                }}
              />
            </Box>
          </MenuItem>

          <Divider sx={{ my: 0.5 }} />

          {[
            {
              key: "connectors",
              icon: "solar:widget-linear",
              label: "Connectors",
              desc: "Connect external tools to enhance evaluations.",
            },
            {
              key: "knowledge",
              icon: "solar:book-linear",
              label: "Knowledge Base",
              desc: "Add context from your knowledge bases",
            },
            {
              key: "injection",
              icon: "solar:layers-linear",
              label: "Data Injection",
              desc: "Control what data is included when running",
            },
            {
              key: "summary",
              icon: "solar:notes-linear",
              label: "Summary",
              desc: "Control how detailed or brief the evaluation output should be",
            },
          ].map((item) => (
            <MenuItem
              key={item.key}
              onClick={() =>
                setPlusSubmenu(plusSubmenu === item.key ? null : item.key)
              }
              selected={plusSubmenu === item.key}
              sx={{ borderRadius: "6px", py: 1 }}
            >
              <Iconify
                icon={item.icon}
                width={20}
                sx={{ mr: 1.5, color: "text.secondary" }}
              />
              <Box sx={{ flex: 1 }}>
                <Typography
                  variant="body2"
                  sx={{ fontSize: "13px", fontWeight: 500 }}
                >
                  {item.label}
                </Typography>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ fontSize: "12px" }}
                >
                  {item.desc}
                </Typography>
              </Box>
              <Iconify
                icon="solar:alt-arrow-right-linear"
                width={16}
                sx={{ color: "text.disabled" }}
              />
            </MenuItem>
          ))}
        </Box>

        {/* Submenu */}
        {plusSubmenu && (
          <Box
            sx={{
              minWidth: 220,
              borderLeft: "1px solid",
              borderColor: "divider",
              pl: 0.5,
            }}
          >
            {plusSubmenu === "connectors" && (
              <>
                {connectors.length > 3 && (
                  <Box sx={{ p: 1 }}>
                    <TextField
                      size="small"
                      fullWidth
                      placeholder="Search connectors"
                      value={connectorSearch}
                      onChange={(e) => setConnectorSearch(e.target.value)}
                      InputProps={{
                        startAdornment: (
                          <Iconify
                            icon="solar:magnifier-linear"
                            width={16}
                            sx={{ mr: 0.5, color: "text.disabled" }}
                          />
                        ),
                        sx: { fontSize: "13px", height: 30 },
                      }}
                    />
                  </Box>
                )}
                {filteredConnectors.length > 0 ? (
                  filteredConnectors.map((conn) => (
                    <MenuItem
                      key={conn.id}
                      onClick={() => handleToggleConnector(conn.id)}
                      sx={{
                        borderRadius: "6px",
                        py: 0.75,
                        gap: 1,
                        justifyContent: "space-between",
                      }}
                    >
                      <Box
                        sx={{ display: "flex", alignItems: "center", gap: 1 }}
                      >
                        <Box
                          sx={{
                            width: 8,
                            height: 8,
                            borderRadius: "50%",
                            backgroundColor: conn.is_verified
                              ? "status.success.solid"
                              : "text.disabled",
                          }}
                        />
                        <Typography variant="body2" sx={{ fontSize: "13px" }}>
                          {conn.name}
                        </Typography>
                      </Box>
                      {activeConnectorIds.includes(conn.id) && (
                        <Iconify
                          icon="solar:check-linear"
                          width={16}
                          sx={{ color: "primary.main" }}
                        />
                      )}
                    </MenuItem>
                  ))
                ) : (
                  <MenuItem disabled sx={{ py: 1 }}>
                    <Typography
                      sx={{
                        fontSize: 13,
                        fontStyle: "italic",
                        color: "text.disabled",
                      }}
                    >
                      No connectors configured
                    </Typography>
                  </MenuItem>
                )}
                <Divider sx={{ my: 0.5 }} />
                <MenuItem
                  onClick={() => {
                    setPlusAnchor(null);
                    setPlusSubmenu(null);
                    navigate("/dashboard/settings/falcon-ai-connectors");
                  }}
                  sx={{ borderRadius: "6px", py: 0.75, gap: 1 }}
                >
                  <Iconify
                    icon="solar:settings-linear"
                    width={16}
                    sx={{ color: "text.secondary" }}
                  />
                  <Typography variant="body2" sx={{ fontSize: "13px" }}>
                    Manage connectors
                  </Typography>
                </MenuItem>
              </>
            )}
            {/* ══ Knowledge Base submenu ══ */}
            {plusSubmenu === "knowledge" && (
              <Box
                sx={{ width: 360, maxHeight: 360, overflowY: "auto" }}
                onScroll={(e) => {
                  const { scrollTop, scrollHeight, clientHeight } = e.target;
                  if (
                    scrollHeight - scrollTop - clientHeight < 50 &&
                    hasNextKBPage &&
                    !isFetchingNextKBPage
                  ) {
                    fetchNextKBPage();
                  }
                }}
              >
                <Box
                  sx={{
                    position: "sticky",
                    top: 0,
                    zIndex: 1,
                    backgroundColor: "background.paper",
                    borderBottom: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Box sx={{ px: 1, pt: 0.75, pb: 0.75 }}>
                    <TextField
                      size="small"
                      fullWidth
                      autoFocus
                      placeholder="Search knowledge bases..."
                      value={kbSearch}
                      onChange={(e) => setKbSearch(e.target.value)}
                      InputProps={{
                        startAdornment: (
                          <Iconify
                            icon="solar:magnifier-linear"
                            width={14}
                            sx={{ mr: 0.5, color: "text.disabled" }}
                          />
                        ),
                        sx: {
                          fontSize: (theme) => theme.typography.s2.fontSize,
                          height: 30,
                        },
                      }}
                    />
                  </Box>

                  {selectedKBs.length > 0 && (
                    <Box
                      sx={{
                        px: 1.25,
                        pb: 0.75,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                      }}
                    >
                      <Typography
                        variant="s3"
                        sx={{
                          fontWeight: "fontWeightSemiBold",
                          color: "text.secondary",
                          letterSpacing: "0.02em",
                        }}
                      >
                        {selectedKBs.length} selected
                      </Typography>
                      <ButtonBase
                        onClick={() => setSelectedKBs([])}
                        sx={{
                          typography: "s3",
                          fontWeight: "fontWeightSemiBold",
                          color: "primary.main",
                          "&:hover": { textDecoration: "underline" },
                        }}
                      >
                        Clear all
                      </ButtonBase>
                    </Box>
                  )}
                </Box>

                {knowledgeBases.length > 0 ? (
                  knowledgeBases.map((kb) => {
                    const kbId = kb.id;
                    const kbName = kb.name;
                    const isSelected = selectedKBs.includes(kbId);
                    return (
                      <MenuItem
                        key={kbId}
                        onClick={() =>
                          setSelectedKBs((prev) =>
                            isSelected
                              ? prev.filter((x) => x !== kbId)
                              : [...prev, kbId],
                          )
                        }
                        sx={{
                          borderRadius: "6px",
                          mx: 0.5,
                          py: 0.5,
                          gap: 1,
                          ...(isSelected && {
                            backgroundColor: "status.discovery.bg",
                          }),
                        }}
                      >
                        <Iconify
                          icon={
                            isSelected
                              ? "solar:check-square-bold"
                              : "solar:square-linear"
                          }
                          width={20}
                          sx={{
                            color: isSelected
                              ? "primary.main"
                              : "text.disabled",
                            flexShrink: 0,
                          }}
                        />
                        <Typography
                          variant="s2_1"
                          noWrap
                          sx={{
                            flex: 1,
                            fontWeight: isSelected
                              ? "fontWeightSemiBold"
                              : "fontWeightRegular",
                            color: isSelected
                              ? "text.primary"
                              : "text.secondary",
                          }}
                        >
                          {kbName}
                        </Typography>
                      </MenuItem>
                    );
                  })
                ) : kbLoading ? (
                  <KBSkeletonRows count={6} />
                ) : (
                  <Box sx={{ py: 2, px: 1.5, textAlign: "center" }}>
                    <Typography
                      sx={{ fontSize: 12, color: "text.disabled", mb: 1 }}
                    >
                      {kbSearch ? "No matches" : "No knowledge bases yet"}
                    </Typography>
                    <Chip
                      size="small"
                      icon={
                        <Iconify
                          icon="solar:arrow-right-up-linear"
                          width={12}
                        />
                      }
                      label="Create in Knowledge Base"
                      onClick={() => {
                        setPlusAnchor(null);
                        setPlusSubmenu(null);
                        navigate("/dashboard/knowledge");
                      }}
                      sx={{ cursor: "pointer" }}
                    />
                  </Box>
                )}
                {isFetchingNextKBPage && <KBSkeletonRows count={3} />}
              </Box>
            )}

            {/* ══ Data Injection submenu ══ */}
            {plusSubmenu === "injection" && (
              <Box>
                {CONTEXT_OPTIONS.filter(
                  (opt) =>
                    !(hideDatasetContextToggle && opt.value === "dataset_row"),
                ).map((opt) => {
                  const isActive = activeContextOptions.includes(opt.value);
                  return (
                    <MenuItem
                      key={opt.value}
                      onClick={() => {
                        setActiveContextOptions((prev) => {
                          if (opt.isDefault) {
                            // Clicking "variables_only" clears any active context.
                            return ["variables_only"];
                          }
                          // Single-select: clicking a non-default option replaces
                          // whatever was active. Clicking the already-active one
                          // toggles it off and reverts to variables_only.
                          if (prev.includes(opt.value)) {
                            return ["variables_only"];
                          }
                          return [opt.value];
                        });
                      }}
                      sx={{
                        borderRadius: "6px",
                        py: 0.6,
                        gap: 1,
                      }}
                    >
                      <Iconify
                        icon={opt.icon}
                        width={16}
                        sx={{
                          color: isActive ? "primary.main" : "text.disabled",
                          flexShrink: 0,
                        }}
                      />
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        <Typography
                          variant="body2"
                          sx={{
                            fontSize: "12px",
                            fontWeight: isActive ? 600 : 400,
                          }}
                        >
                          {opt.label}
                        </Typography>
                        <Typography
                          variant="caption"
                          sx={{
                            fontSize: "12px",
                            color: "text.disabled",
                            lineHeight: 1.2,
                            display: "block",
                          }}
                        >
                          {opt.desc}
                        </Typography>
                      </Box>
                      <Box
                        sx={{
                          width: 32,
                          height: 16,
                          borderRadius: 8,
                          position: "relative",
                          backgroundColor: isActive
                            ? "primary.main"
                            : "control.track",
                          transition: "all 0.2s",
                          flexShrink: 0,
                          cursor: "pointer",
                        }}
                      >
                        <Box
                          sx={{
                            width: 12,
                            height: 12,
                            borderRadius: "50%",
                            backgroundColor: "control.thumb",
                            position: "absolute",
                            top: 2,
                            left: isActive ? 18 : 2,
                            transition: "left 0.2s",
                          }}
                        />
                      </Box>
                    </MenuItem>
                  );
                })}
              </Box>
            )}
            {plusSubmenu === "summary" && (
              <SummarySubmenu
                activeSummary={activeSummary}
                onSelect={(val) => {
                  setActiveSummary(val);
                  setPlusSubmenu(null);
                  setPlusAnchor(null);
                }}
              />
            )}
          </Box>
        )}
      </Popover>
      {/* ══════ Keys Drawer ══════ */}
      <KeysDrawer
        open={Boolean(keysDrawerModel)}
        selectedModel={keysDrawerModel}
        onClose={() => setKeysDrawerModel(null)}
      />
    </Box>
  );
};

ModelSelector.propTypes = {
  model: PropTypes.string.isRequired,
  onModelChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  showMode: PropTypes.bool,
  showPlus: PropTypes.bool,
  hideDatasetContextToggle: PropTypes.bool,
  openModelMenuSignal: PropTypes.number,
};

export default ModelSelector;

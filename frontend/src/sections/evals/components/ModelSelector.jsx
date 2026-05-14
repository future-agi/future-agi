/* eslint-disable react/prop-types */
import {
  Box,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  MenuItem,
  Popover,
  TextField,
  Tooltip,
  Typography,
  alpha,
} from "@mui/material";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import KeysDrawer from "src/components/custom-model-dropdown/KeysDrawer";
import { useDebounce } from "src/hooks/use-debounce";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { useNavigate } from "react-router";
import axios, { endpoints } from "src/utils/axios";

// ---------------------------------------------------------------------------
// Modes — how the evaluator runs
// ---------------------------------------------------------------------------
const MODES = [
  {
    value: "auto",
    label: "Auto",
    description: "Balances quality and speed",
    icon: "mdi:auto-fix",
  },
  {
    value: "agent",
    label: "Agent",
    description: "Deep, reasoning-based evaluation",
    icon: "mdi:robot-outline",
  },
  {
    value: "quick",
    label: "Quick",
    description: "Runs fast, for quick iteration",
    icon: "mdi:lightning-bolt-outline",
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
    icon: "mdi:code-braces",
    isDefault: true,
  },
  {
    value: "dataset_row",
    label: "Dataset row context",
    desc: "All columns from current row",
    icon: "mdi:table-row",
  },
  {
    value: "call_context",
    label: "Call context",
    desc: "Call transcript, recording, scenario",
    icon: "mdi:phone-outline",
  },
  {
    value: "span_context",
    label: "Full span context",
    desc: "Complete span data + metadata",
    icon: "mdi:layers-outline",
  },
  {
    value: "trace_context",
    label: "Trace context",
    desc: "Full trace tree with all spans",
    icon: "mdi:file-tree-outline",
  },
  {
    value: "session_context",
    label: "Session context",
    desc: "Full conversation history",
    icon: "mdi:message-text-outline",
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
    icon: "mdi:star-circle",
  },
  {
    value: "turing_small",
    label: "Turing Small",
    description: "Balanced accuracy, lower cost",
    icon: "mdi:star-half-full",
  },
  {
    value: "turing_flash",
    label: "Turing Flash",
    description: "Fast, low-latency evaluations",
    icon: "mdi:flash",
  },
];

export const FAGI_MODEL_VALUES = new Set(FAGI_MODELS.map((m) => m.value));

const CHIP_STYLES = {
  backgroundColor: (theme) =>
    alpha(
      theme.palette.primary.main,
      theme.palette.mode === "dark" ? 0.24 : 0.1,
    ),
  "&:hover": {
    backgroundColor: (theme) =>
      alpha(
        theme.palette.primary.main,
        theme.palette.mode === "dark" ? 0.32 : 0.16,
      ),
  },
  color: (theme) =>
    theme.palette.mode === "dark"
      ? theme.palette.primary.light
      : theme.palette.primary.main,
  border: "1px solid",
  borderColor: (theme) =>
    alpha(
      theme.palette.primary.main,
      theme.palette.mode === "dark" ? 0.4 : 0.2,
    ),
  borderRadius: "4px",
  fontWeight: 500,
  fontSize: "11px",
  height: 22,
  "& .MuiChip-label": { px: 0.75 },
  "& .MuiChip-icon": {
    color: "inherit",
  },
  "& .MuiChip-deleteIcon": {
    margin: "0 4px 0 -2px",
    color: (theme) =>
      theme.palette.mode === "dark"
        ? theme.palette.primary.light
        : theme.palette.primary.main,
    transition: "color 0.15s ease",
    "&:hover": {
      color: (theme) =>
        theme.palette.mode === "dark"
          ? theme.palette.primary.contrastText
          : theme.palette.primary.dark,
    },
  },
};

const DELETE_ICON = <Iconify icon="mdi:close" width={12} />;

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
            fontSize: "10px",
            fontWeight: 700,
            textTransform: "uppercase",
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
            <Iconify icon="mdi:close" width={16} />
          </IconButton>
          <IconButton
            size="small"
            onClick={handleSave}
            disabled={
              !editName.trim() || !editCriteria.trim() || saveMutation.isLoading
            }
            sx={{ color: "primary.main" }}
          >
            <Iconify icon="mdi:check" width={16} />
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
          icon={!activeSummary ? "mdi:check" : "mdi:blank"}
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
            sx={{ fontSize: "11px" }}
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
              icon={isSelected ? "mdi:check" : "mdi:blank"}
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
                sx={{ fontSize: "11px" }}
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
              fontSize: "10px",
              fontWeight: 700,
              textTransform: "uppercase",
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
                  icon={isSelected ? "mdi:check" : "mdi:blank"}
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
                    sx={{ fontSize: "11px" }}
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
                    sx={{ p: 0.25 }}
                  >
                    <Iconify
                      icon="mdi:pencil-outline"
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
                    sx={{ p: 0.25 }}
                  >
                    <Iconify
                      icon="mdi:delete-outline"
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
        <Iconify icon="mdi:plus" width={16} sx={{ color: "text.secondary" }} />
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
  const [keysDrawerModel, setKeysDrawerModel] = useState(null);
  const navigate = useNavigate();
  const { isOSS } = useDeploymentMode();

  const currentMode = MODES.find((m) => m.value === mode) || MODES[1];

  // Fetch real MCP connectors from Falcon AI API. Falcon AI is EE-only;
  // skip the call in OSS so the ee_stub 402 doesn't fire a snackbar.
  const { data: connectorsData } = useInfiniteQuery({
    queryKey: ["falcon-mcp-connectors"],
    queryFn: () => axios.get(endpoints.falconAI.connectors),
    getNextPageParam: () => null,
    initialPageParam: 1,
    staleTime: 60000,
    enabled: !isOSS,
  });

  const connectors = useMemo(() => {
    const results =
      connectorsData?.pages?.[0]?.data?.results ||
      connectorsData?.pages?.[0]?.data ||
      [];
    return Array.isArray(results) ? results : [];
  }, [connectorsData]);

  // Fetch knowledge bases
  const { data: kbData } = useInfiniteQuery({
    queryKey: ["eval-knowledge-bases"],
    queryFn: () => axios.get(endpoints.knowledge.list),
    getNextPageParam: () => null,
    initialPageParam: 1,
    staleTime: 60000,
  });

  const knowledgeBases = useMemo(() => {
    const page = kbData?.pages?.[0]?.data;
    // API returns {status, result: {tableData: [...], totalRows}}
    const items =
      page?.result?.tableData || page?.results || page?.tableData || [];
    return Array.isArray(items) ? items : [];
  }, [kbData]);

  const filteredKBs = useMemo(() => {
    if (!kbSearch) return knowledgeBases;
    return knowledgeBases.filter((kb) =>
      (kb.name || kb.title || "")
        .toLowerCase()
        .includes(kbSearch.toLowerCase()),
    );
  }, [knowledgeBases, kbSearch]);

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
        params: { page: pageParam, search: debouncedModelSearch, model_type: "llm" },
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

  // Filter FAGI models by search — always shown in both OSS and EE
  const filteredFagiModels = modelSearch
    ? FAGI_MODELS.filter((m) =>
        m.label.toLowerCase().includes(modelSearch.toLowerCase()),
      )
    : FAGI_MODELS;

  return (
    <Box
      sx={{ display: "flex", alignItems: "center", gap: 0.5, flexWrap: "wrap" }}
    >
      {/* ── Mode selector (left pill) — hidden in LLM-As-A-Judge ── */}
      {showMode && (
        <Box
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
            borderRadius: "16px",
            cursor: disabled ? "default" : "pointer",
            backgroundColor: (theme) =>
              theme.palette.mode === "dark"
                ? "rgba(255,255,255,0.05)"
                : "rgba(0,0,0,0.03)",
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
            icon="mdi:chevron-down"
            width={14}
            sx={{ color: "text.disabled" }}
          />
        </Box>
      )}

      {/* ── Model selector (right) ── */}
      <Box
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
          icon="mdi:chevron-down"
          width={14}
          sx={{ color: "text.disabled" }}
        />
      </Box>

      {/* ── + button + config chips (hidden when showPlus=false, e.g. LLM tab) ── */}
      {showPlus && (
        <IconButton
          size="small"
          disabled={disabled}
          onClick={(e) => setPlusAnchor(e.currentTarget)}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "6px",
            width: 26,
            height: 26,
          }}
        >
          <Iconify icon="mdi:plus" width={14} />
        </IconButton>
      )}

      {/* ── Active config chips (only when + menu is available) ── */}
      {showPlus && useInternet && (
        <Chip
          size="small"
          icon={<Iconify icon="mdi:web" width={12} sx={{ ml: 0.5 }} />}
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
            height: 22,
            fontSize: "11px",
            fontWeight: 500,
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
                    height: 7,
                    borderRadius: "50%",
                    ml: 0.5,
                    backgroundColor: conn.is_verified
                      ? "success.main"
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
              <Typography sx={{ fontSize: "11px", fontWeight: 600, mb: 0.5 }}>
                Knowledge Bases
              </Typography>
              {selectedKBs.map((kbId) => {
                const kb = knowledgeBases.find((k) => (k.id || k.pk) === kbId);
                return (
                  <Typography key={kbId} sx={{ fontSize: "11px" }}>
                    • {kb?.name || kb?.title || "KB"}
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
                icon="mdi:book-open-outline"
                width={12}
                sx={{ ml: 0.5 }}
              />
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
                <Typography sx={{ fontSize: "11px", fontWeight: 600, mb: 0.5 }}>
                  Data Injection
                </Typography>
                {activeContextOptions
                  .filter((o) => o !== "variables_only")
                  .map((optVal) => {
                    const opt = CONTEXT_OPTIONS.find((c) => c.value === optVal);
                    return (
                      <Typography key={optVal} sx={{ fontSize: "11px" }}>
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
                  icon="mdi:layers-outline"
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
              width={18}
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
                sx={{ fontSize: "11px" }}
              >
                {m.description}
              </Typography>
            </Box>
            {m.value === mode && (
              <Iconify
                icon="mdi:check"
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
                  icon="mdi:magnify"
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
                  fontSize: "10px",
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                  color: "primary.main",
                }}
              >
                FutureAGI Models
              </Typography>
              {filteredFagiModels.map((m) => (
                <MenuItem
                  key={m.value}
                  selected={m.value === model}
                  onClick={() => {
                    onModelChange(m.value);
                    setModelAnchor(null);
                    setModelSearch("");
                  }}
                  sx={{ mx: 0.5, borderRadius: "6px", py: 0.75, gap: 1 }}
                >
                  <Iconify
                    icon={m.icon}
                    width={18}
                    sx={{
                      color:
                        m.value === model ? "primary.main" : "text.secondary",
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
                      sx={{ fontSize: "11px" }}
                    >
                      {m.description}
                    </Typography>
                  </Box>
                  {m.value === model && (
                    <Iconify
                      icon="mdi:check"
                      width={16}
                      sx={{ color: "primary.main", flexShrink: 0 }}
                    />
                  )}
                </MenuItem>
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
                  fontSize: "10px",
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                  color: "text.disabled",
                }}
              >
                Your Models
              </Typography>
              {apiModels.map((m) => {
                const available = m.isAvailable !== false;
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
                        "&:hover": { backgroundColor: "error.lighter" },
                      }),
                    }}
                  >
                    {m.logoUrl ? (
                      <Box
                        component="img"
                        src={m.logoUrl}
                        sx={{
                          width: 18,
                          height: 18,
                          borderRadius: "4px",
                          flexShrink: 0,
                        }}
                      />
                    ) : (
                      <Iconify
                        icon="mdi:cube-outline"
                        width={18}
                        sx={{
                          color: available ? "text.secondary" : "error.main",
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
                          color: available ? "text.primary" : "error.main",
                        }}
                      >
                        {m.model_name}
                      </Typography>
                      {m.providers && (
                        <Typography
                          variant="caption"
                          noWrap
                          sx={{
                            fontSize: "11px",
                            color: available ? "text.secondary" : "error.light",
                          }}
                        >
                          {m.providers}
                        </Typography>
                      )}
                    </Box>
                    {!available ? (
                      <Iconify
                        icon="mdi:key-plus"
                        width={16}
                        sx={{ color: "error.main", flexShrink: 0 }}
                      />
                    ) : m.model_name === model ? (
                      <Iconify
                        icon="mdi:check"
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
              icon="mdi:web"
              width={18}
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
                sx={{ fontSize: "11px" }}
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
                backgroundColor: useInternet
                  ? "primary.main"
                  : (theme) =>
                      theme.palette.mode === "dark"
                        ? "rgba(255,255,255,0.12)"
                        : "rgba(0,0,0,0.12)",
                transition: "all 0.2s",
                flexShrink: 0,
              }}
            >
              <Box
                sx={{
                  width: 12,
                  height: 12,
                  borderRadius: "50%",
                  backgroundColor: "#fff",
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
              icon: "mdi:puzzle-outline",
              label: "Connectors",
              desc: "Connect external tools to enhance evaluations.",
            },
            {
              key: "knowledge",
              icon: "mdi:book-open-outline",
              label: "Knowledge Base",
              desc: "Add context from your knowledge bases",
            },
            {
              key: "injection",
              icon: "mdi:layers-outline",
              label: "Data Injection",
              desc: "Control what data is included when running",
            },
            {
              key: "summary",
              icon: "mdi:text-short",
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
                width={18}
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
                  sx={{ fontSize: "11px" }}
                >
                  {item.desc}
                </Typography>
              </Box>
              <Iconify
                icon="mdi:chevron-right"
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
                            icon="mdi:magnify"
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
                              ? "success.main"
                              : "text.disabled",
                          }}
                        />
                        <Typography variant="body2" sx={{ fontSize: "13px" }}>
                          {conn.name}
                        </Typography>
                      </Box>
                      {activeConnectorIds.includes(conn.id) && (
                        <Iconify
                          icon="mdi:check"
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
                    icon="mdi:cog-outline"
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
              <Box sx={{ maxHeight: 350, overflowY: "auto" }}>
                <Box sx={{ px: 1, pt: 0.5, pb: 0.5 }}>
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
                          icon="mdi:magnify"
                          width={14}
                          sx={{ mr: 0.5, color: "text.disabled" }}
                        />
                      ),
                      sx: { fontSize: "12px", height: 28 },
                    }}
                  />
                </Box>

                {selectedKBs.length > 0 && (
                  <Box
                    sx={{
                      px: 1,
                      pb: 0.5,
                      display: "flex",
                      flexWrap: "wrap",
                      gap: 0.5,
                    }}
                  >
                    {selectedKBs.map((kbId) => {
                      const kb = knowledgeBases.find(
                        (k) => (k.id || k.pk) === kbId,
                      );
                      return (
                        <Chip
                          key={kbId}
                          size="small"
                          label={kb?.name || kb?.title || "KB"}
                          onDelete={() =>
                            setSelectedKBs((prev) =>
                              prev.filter((x) => x !== kbId),
                            )
                          }
                          deleteIcon={DELETE_ICON}
                          sx={{ ...CHIP_STYLES, height: 20 }}
                        />
                      );
                    })}
                  </Box>
                )}

                {filteredKBs.length > 0 ? (
                  filteredKBs.map((kb) => {
                    const kbId = kb.id || kb.pk;
                    const kbName = kb.name || kb.title || "Untitled";
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
                        sx={{ borderRadius: "6px", py: 0.5, gap: 1 }}
                      >
                        <Iconify
                          icon={
                            isSelected
                              ? "mdi:checkbox-marked"
                              : "mdi:checkbox-blank-outline"
                          }
                          width={18}
                          sx={{
                            color: isSelected
                              ? "primary.main"
                              : "text.disabled",
                            flexShrink: 0,
                          }}
                        />
                        <Typography
                          variant="body2"
                          noWrap
                          sx={{ fontSize: "13px", flex: 1 }}
                        >
                          {kbName}
                        </Typography>
                      </MenuItem>
                    );
                  })
                ) : (
                  <Box sx={{ py: 2, px: 1.5, textAlign: "center" }}>
                    <Typography
                      sx={{ fontSize: 12, color: "text.disabled", mb: 1 }}
                    >
                      {kbSearch ? "No matches" : "No knowledge bases yet"}
                    </Typography>
                    <Chip
                      size="small"
                      icon={<Iconify icon="mdi:open-in-new" width={12} />}
                      label="Create in Knowledge Base"
                      onClick={() => {
                        setPlusAnchor(null);
                        setPlusSubmenu(null);
                        navigate("/dashboard/knowledge");
                      }}
                      sx={{ fontSize: "11px", cursor: "pointer" }}
                    />
                  </Box>
                )}
              </Box>
            )}

            {/* ══ Data Injection submenu ══ */}
            {plusSubmenu === "injection" && (
              <Box>
                {CONTEXT_OPTIONS.map((opt) => {
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
                            fontSize: "10px",
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
                            : (theme) =>
                                theme.palette.mode === "dark"
                                  ? "rgba(255,255,255,0.25)"
                                  : "rgba(0,0,0,0.12)",
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
                            backgroundColor: isActive
                              ? "#fff"
                              : (theme) =>
                                  theme.palette.mode === "dark"
                                    ? "rgba(255,255,255,0.7)"
                                    : "#fff",
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
};

export default ModelSelector;

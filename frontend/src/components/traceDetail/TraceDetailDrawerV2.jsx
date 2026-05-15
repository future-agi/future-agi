import React, { useState, useCallback, useEffect, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  CircularProgress,
  Drawer,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import DrawerHeader from "./DrawerHeader";
import DrawerToolbar from "./DrawerToolbar";
import TraceDisplayPanel, { DEFAULT_VIEW_CONFIG } from "./TraceDisplayPanel";
import {
  useGetSavedViews,
  useCreateSavedView,
  useUpdateSavedView,
  useDeleteSavedView,
  useReorderSavedViews,
} from "src/api/project/saved-views";
import SpanTreeTimeline from "./SpanTreeTimeline";
import SpanDetailPane from "./SpanDetailPane";
import LeftPanelSplit from "./TraceLeftPanel";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import FalconAISidebar from "src/sections/falcon-ai/FalconAISidebar";
import {
  formatLatency,
  formatTokenCount,
  formatCost,
} from "src/sections/projects/LLMTracing/formatters";
import { enqueueSnackbar } from "notistack";
import { ShareDialog } from "src/components/share-dialog";
import AddToQueueDialog from "src/sections/annotations/queues/components/add-to-queue-dialog";
import AddDataset from "src/components/traceDetailDrawer/addToDataset/add-dataset";
import AnnotationSidebarContent from "src/components/traceDetailDrawer/AnnotationSidebarContent";
import AddLabelDrawer from "src/components/traceDetailDrawer/AddLabelDrawer";
import AddTagsPopover from "./AddTagsPopover";
import SaveViewPopover from "./SaveViewDialog";
import { useNavigate } from "react-router";
import TraceFilterPanel from "src/sections/projects/LLMTracing/TraceFilterPanel";
import ImagineTab from "src/components/imagine/ImagineTab";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import useImagineStore from "src/components/imagine/useImagineStore";

const PANEL_WIDTH = "60vw";

const SPAN_FILTER_DEFAULT = {
  columnId: "",
  filterConfig: { filterType: "", filterOp: "", filterValue: "" },
};

const ACTION_ITEMS = [
  { id: "dataset", label: "Move to dataset", icon: "mdi:database-outline" },
  {
    id: "workbench",
    label: "Iterate in prompt workbench",
    icon: "mdi:pencil-box-outline",
    spanTypes: ["llm"],
  },
  // {
  //   id: "playground",
  //   label: "Iterate in agent playground",
  //   icon: "mdi:gamepad-variant-outline",
  // },
  { id: "tags", label: "Add Tags", icon: "mdi:tag-outline" },
  { id: "annotate", label: "Annotate", icon: "mdi:comment-text-outline" },
  { id: "queue", label: "Add to annotation queue", icon: "mdi:playlist-plus" },
];

/**
 * Parse template variables from span attributes.
 * SDK stores them as JSON string in:
 *   - gen_ai.prompt.template.variables
 *   - llm.prompt_template.variables
 * Returns { varName: value } or null.
 */
function parseTemplateVariables(attrs) {
  const raw =
    attrs?.["gen_ai.prompt.template.variables"] ||
    attrs?.["llm.prompt_template.variables"];
  if (!raw) return null;
  try {
    const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
      return parsed;
  } catch {
    /* ignore */
  }
  return null;
}

/**
 * Replace resolved variable values in text with {{varName}} placeholders.
 * Sorts by value length descending so longer values get replaced first
 * (avoids partial matches when one value is a substring of another).
 */
function templatizeText(text, variables) {
  if (!text || !variables) return text;
  let result = text;
  const entries = Object.entries(variables)
    .filter(([, val]) => val != null && String(val).length > 0)
    .sort((a, b) => String(b[1]).length - String(a[1]).length);
  for (const [name, value] of entries) {
    const strVal = String(value);
    // Replace all occurrences of the value with {{name}}
    let idx = result.indexOf(strVal);
    while (idx !== -1) {
      result =
        result.slice(0, idx) +
        `{{${name}}}` +
        result.slice(idx + strVal.length);
      idx = result.indexOf(strVal, idx + `{{${name}}}`.length);
    }
  }
  return result;
}

/**
 * Extract LLM input messages from span attributes and convert to
 * the workbench prompt_config format.
 * If template variables are found, replaces resolved values with
 * {{variable}} placeholders and returns variable_names for the draft.
 */
function buildPromptConfigFromSpan(span) {
  const attrs = span?.span_attributes || span?.eval_attributes || {};
  const templateVars = parseTemplateVariables(attrs);

  // Parse input messages from flattened attributes
  const tempMessages = {};
  const messagePrefixes = [
    "llm.inputMessages",
    "llm.input_messages",
    "gen_ai.input.messages",
  ];

  Object.keys(attrs).forEach((key) => {
    const matchingPrefix = messagePrefixes.find((prefix) =>
      key.startsWith(prefix),
    );
    if (!matchingPrefix) return;
    const parts = key.replace(`${matchingPrefix}.`, "").split(".");
    const index = parts[0];
    const property = parts.slice(1).join(".");
    if (!tempMessages[index]) tempMessages[index] = {};
    if (property === "message.role" || property === "role") {
      tempMessages[index].role = attrs[key];
    }
    if (
      property.startsWith("message.content") ||
      property.startsWith("content")
    ) {
      let content = attrs[key];
      if (typeof content === "object" && content !== null)
        content = JSON.stringify(content, null, 2);
      if (!tempMessages[index].content) tempMessages[index].content = content;
      else if (typeof tempMessages[index].content === "string") {
        tempMessages[index].content += content;
      }
    }
  });

  const parsedMessages = Object.keys(tempMessages)
    .sort((a, b) => parseInt(a) - parseInt(b))
    .filter((key) => tempMessages[key].role)
    .map((key) => {
      let text =
        typeof tempMessages[key].content === "string"
          ? tempMessages[key].content
          : JSON.stringify(tempMessages[key].content ?? "", null, 2);
      // Replace resolved values with {{variable}} placeholders
      if (templateVars) {
        text = templatizeText(text, templateVars);
      }
      return {
        role: tempMessages[key].role,
        content: [{ type: "text", text }],
      };
    });

  // Fallback: if no messages parsed from attributes, use span.input
  if (parsedMessages.length === 0 && span?.input) {
    let inputContent =
      typeof span.input === "string"
        ? span.input
        : JSON.stringify(span.input, null, 2);
    if (templateVars) {
      inputContent = templatizeText(inputContent, templateVars);
    }
    parsedMessages.push(
      { role: "system", content: [{ type: "text", text: "" }] },
      { role: "user", content: [{ type: "text", text: inputContent }] },
    );
  }

  // Ensure system message exists
  if (parsedMessages.length > 0 && parsedMessages[0].role !== "system") {
    parsedMessages.unshift({
      role: "system",
      content: [{ type: "text", text: "" }],
    });
  }

  // Build variable_names dict: { varName: [sampleValue] }
  const variableNames = {};
  if (templateVars) {
    for (const [name, value] of Object.entries(templateVars)) {
      variableNames[name] = value != null ? [String(value)] : [];
    }
  }

  return { messages: parsedMessages, variableNames };
}

function getSpan(entry) {
  return entry?.observation_span || {};
}

const TraceDetailDrawerV2 = ({
  traceId,
  open,
  onClose,
  projectId,
  onPrev,
  onNext,
  hasPrev = true,
  hasNext = true,
  initialFullscreen = false,
  initialSpanId = null,
  refreshParentGrid,
}) => {
  const navigate = useNavigate();

  const { mutate: createPromptDraft, isPending: isCreatingDraft } = useMutation(
    {
      mutationFn: (body) =>
        axios.post(endpoints.develop.runPrompt.createPromptDraft, body),
      onSuccess: (res) => {
        const newId =
          res?.data?.result?.rootTemplate ||
          res?.data?.result?.root_template ||
          res?.data?.result?.id;
        if (newId) {
          navigate(`/dashboard/workbench/create/${newId}?tab=Playground`);
        }
      },
      onError: () => {
        enqueueSnackbar("Failed to create prompt. Please try again.", {
          variant: "error",
        });
      },
    },
  );

  const { mutate: createGraphFromTrace, isPending: isCreatingGraph } =
    useMutation({
      mutationFn: (body) =>
        axios.post(endpoints.agentPlayground.createGraphFromTrace, body),
      onSuccess: (res) => {
        const result = res?.data?.result;
        if (result?.graph_id || result?.graphId) {
          const graphId = result.graph_id || result.graphId;
          const versionId = result.version_id || result.versionId;
          navigate(
            `/dashboard/agents/playground/${graphId}/build?version=${versionId}`,
          );
        }
      },
      onError: () => {
        enqueueSnackbar("Failed to create agent graph from trace.", {
          variant: "error",
        });
      },
    });

  const [selectedSpanId, setSelectedSpanId] = useState(null);
  const [viewMode, setViewMode] = useState(() => {
    try {
      const raw = localStorage.getItem(`trace-view-default-${projectId}`);
      return raw ? JSON.parse(raw).viewMode || "tree" : "tree";
    } catch {
      return "tree";
    }
  });
  const [leftPanelWidth, setLeftPanelWidth] = useState(40); // percentage
  const [actionsAnchorEl, setActionsAnchorEl] = useState(null);
  const [isFullscreen, setIsFullscreen] = useState(initialFullscreen);
  const [drawerWidth, setDrawerWidth] = useState(60); // percentage of viewport
  const [shareDialogOpen, setShareDialogOpen] = useState(false);

  // When the Falcon sidebar is open (e.g. user clicked "Fix with Falcon"),
  // the trace drawer slides left by SIDEBAR_WIDTH so the sidebar is visible
  // on the right instead of being hidden behind this drawer.
  const isFalconOpen = useFalconStore((s) => s.isSidebarOpen);
  const falconOffsetPx = isFalconOpen ? FalconAISidebar.SIDEBAR_WIDTH : 0;

  // Action dialog/drawer state
  const [queueAnchorEl, setQueueAnchorEl] = useState(null);
  const [datasetDrawerOpen, setDatasetDrawerOpen] = useState(false);
  const [annotateDrawerOpen, setAnnotateDrawerOpen] = useState(null);
  const [addLabelDrawerOpen, setAddLabelDrawerOpen] = useState(false);
  const [tagsAnchorEl, setTagsAnchorEl] = useState(null);

  // Display panel state — load defaults from localStorage
  const storageKey = `trace-view-default-${projectId}`;
  const savedDefault = useMemo(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }, [storageKey]);

  const [displayAnchorEl, setDisplayAnchorEl] = useState(null);
  const [spanTypeFilter, setSpanTypeFilter] = useState(
    savedDefault?.spanTypeFilter ?? DEFAULT_VIEW_CONFIG.spanTypeFilter,
  );
  const [visibleMetrics, setVisibleMetrics] = useState(
    savedDefault?.visibleMetrics ?? DEFAULT_VIEW_CONFIG.visibleMetrics,
  );
  const [showAgentGraph, setShowAgentGraph] = useState(
    savedDefault?.showAgentGraph ?? DEFAULT_VIEW_CONFIG.showAgentGraph,
  );

  // Span filter state — load from localStorage default if available
  const [spanFilters, setSpanFilters] = useState(
    () => savedDefault?.filters || [],
  );
  const [filterAnchorEl, setFilterAnchorEl] = useState(null);

  // ---------------------------------------------------------------------------
  // Saved Views — auto-save per tab
  // ---------------------------------------------------------------------------
  const { data: savedViewsData } = useGetSavedViews(projectId);
  const { mutate: createSavedView } = useCreateSavedView(projectId);
  const { mutate: updateSavedView } = useUpdateSavedView(projectId);
  const { mutate: deleteSavedView } = useDeleteSavedView(projectId);
  const { mutate: reorderSavedViews } = useReorderSavedViews(projectId);

  const customViews =
    savedViewsData?.customViews || savedViewsData?.custom_views || [];

  const [activeDrawerTab, setActiveDrawerTab] = useState("trace");
  const [deleteTabId, setDeleteTabId] = useState(null);

  const drawerTabs = useMemo(() => {
    const tabs = [
      {
        id: "trace",
        label: "Trace",
        icon: "mdi:link-variant",
        isDefault: true,
      },
    ];
    // Only show "imagine" tabs in the trace detail drawer — "traces" tabs belong to the list page
    customViews
      .filter((v) => (v.tab_type || v.tabType) === "imagine")
      .forEach((v) => {
        tabs.push({
          id: v.id,
          label: v.name,
          icon: "mdi:creation",
          isDefault: false,
          config: v.config,
          visibility: v.visibility,
          tabType: "imagine",
        });
      });
    // Include the unsaved Imagine tab so it appears in the toolbar with a close button
    if (activeDrawerTab === "__new_imagine__") {
      tabs.push({
        id: "__new_imagine__",
        label: "Imagine",
        icon: "mdi:creation",
        isDefault: false,
        tabType: "imagine",
      });
    }
    return tabs;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customViews, activeDrawerTab]);

  // Apply saved view config when switching tabs
  const handleTabChange = useCallback(
    (tabId) => {
      setActiveDrawerTab(tabId);
      if (tabId === "trace") {
        // Reset to user's default or hardcoded default
        const def = savedDefault || DEFAULT_VIEW_CONFIG;
        setViewMode(def.viewMode || "tree");
        setSpanTypeFilter(def.spanTypeFilter ?? null);
        setVisibleMetrics(
          def.visibleMetrics || DEFAULT_VIEW_CONFIG.visibleMetrics,
        );
        setShowAgentGraph(def.showAgentGraph ?? true);
        setSpanFilters(def.filters || []);
        return;
      }
      const tab = drawerTabs.find((t) => t.id === tabId);
      if (tab?.config) {
        const display = tab.config.display || tab.config;
        if (display.viewMode) setViewMode(display.viewMode);
        if (display.spanTypeFilter !== undefined)
          setSpanTypeFilter(display.spanTypeFilter);
        if (display.visibleMetrics) setVisibleMetrics(display.visibleMetrics);
        if (display.showAgentGraph !== undefined)
          setShowAgentGraph(display.showAgentGraph);
        setSpanFilters(tab.config.filters || []);
      }
    },
    [drawerTabs, savedDefault],
  );

  // Auto-save display settings when they change (debounced)
  const autoSaveTimerRef = React.useRef(null);
  useEffect(() => {
    if (activeDrawerTab === "trace") {
      // For default tab, persist to localStorage (display + filters)
      const config = {
        viewMode,
        spanTypeFilter,
        visibleMetrics,
        showAgentGraph,
        filters: spanFilters,
      };
      localStorage.setItem(storageKey, JSON.stringify(config));
      return;
    }
    // Skip auto-save for imagine tabs (they save via their own Save View button)
    if (isImagineActive) return;
    // For custom tabs, auto-save to backend (debounced)
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => {
      const config = {
        display: { viewMode, spanTypeFilter, visibleMetrics, showAgentGraph },
        filters: spanFilters,
      };
      updateSavedView({ id: activeDrawerTab, config });
    }, 1000);
    return () => {
      if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    };
  }, [
    viewMode,
    spanTypeFilter,
    visibleMetrics,
    showAgentGraph,
    spanFilters,
    activeDrawerTab,
    storageKey,
    updateSavedView,
  ]);

  // Reset view to defaults
  const handleResetView = useCallback(() => {
    setViewMode(DEFAULT_VIEW_CONFIG.viewMode);
    setSpanTypeFilter(DEFAULT_VIEW_CONFIG.spanTypeFilter);
    setVisibleMetrics(DEFAULT_VIEW_CONFIG.visibleMetrics);
    setShowAgentGraph(DEFAULT_VIEW_CONFIG.showAgentGraph);
  }, []);

  // "Set default for everyone" — make current view project-visible
  const handleSetDefaultView = useCallback(() => {
    if (activeDrawerTab !== "trace") {
      // Update existing custom view to project visibility
      updateSavedView(
        { id: activeDrawerTab, visibility: "project" },
        {
          onSuccess: () =>
            enqueueSnackbar("View set as default for everyone", {
              variant: "success",
            }),
        },
      );
    } else {
      // Create a new project-level view from current config
      const config = {
        display: { viewMode, spanTypeFilter, visibleMetrics, showAgentGraph },
        filters: spanFilters,
      };
      createSavedView(
        {
          project_id: projectId,
          name: "Default View",
          tab_type: "traces",
          visibility: "project",
          config,
        },
        {
          onSuccess: () =>
            enqueueSnackbar("View set as default for everyone", {
              variant: "success",
            }),
        },
      );
    }
  }, [
    activeDrawerTab,
    viewMode,
    spanTypeFilter,
    visibleMetrics,
    showAgentGraph,
    spanFilters,
    projectId,
    updateSavedView,
    createSavedView,
  ]);

  // Create new view from current config (called from "+" button or filter save)
  // Save View popover state
  const [saveViewAnchor, setSaveViewAnchor] = useState(null);
  const [isSavingView, setIsSavingView] = useState(false);

  const handleCreateView = useCallback((e) => {
    setSaveViewAnchor(e?.currentTarget || e || document.body);
  }, []);

  const handleSaveViewConfirm = useCallback(
    (name) => {
      setIsSavingView(true);
      const config = {
        display: { viewMode, spanTypeFilter, visibleMetrics, showAgentGraph },
        filters: spanFilters,
      };
      createSavedView(
        {
          project_id: projectId,
          name,
          tab_type: "traces",
          config,
        },
        {
          onSuccess: (res) => {
            enqueueSnackbar("View created", { variant: "success" });
            const newId = res?.data?.result?.id;
            if (newId) setActiveDrawerTab(newId);
            setSaveViewAnchor(null);
            setIsSavingView(false);
          },
          onError: () => {
            enqueueSnackbar("Failed to create view", { variant: "error" });
            setIsSavingView(false);
          },
        },
      );
    },
    [
      viewMode,
      spanTypeFilter,
      visibleMetrics,
      showAgentGraph,
      spanFilters,
      projectId,
      createSavedView,
    ],
  );

  // Close (delete) a custom view tab
  const handleCloseTab = useCallback((tabId) => {
    if (tabId === "trace") return;
    // Unsaved Imagine tab — just switch back, no API call needed
    if (tabId === "__new_imagine__") {
      setActiveDrawerTab("trace");
      return;
    }
    setDeleteTabId(tabId);
  }, []);

  // Create a new Imagine tab (temporary until saved)
  const handleCreateImagineTab = useCallback(() => {
    useImagineStore.getState().reset();
    setActiveDrawerTab("__new_imagine__");
  }, []);

  const queryClient = useQueryClient();
  const { data, isLoading } = useGetTraceDetail(open ? traceId : null);

  const handleRefresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["trace-detail", traceId] });
  }, [queryClient, traceId]);

  const rawSpans = data?.observation_spans;
  const summary = data?.summary;
  const graph = data?.graph;

  // Apply span type filter + span filters to the tree
  const spans = useMemo(() => {
    if (!rawSpans?.length) return rawSpans;

    // First apply span type filter from Display panel
    const hasTypeFilter =
      spanTypeFilter &&
      Array.isArray(spanTypeFilter) &&
      spanTypeFilter.length > 0;

    // Then apply span filters from Filter panel
    const activeFilters = (spanFilters || []).filter(
      (f) => f.field && Array.isArray(f.value) && f.value.length > 0,
    );

    if (!hasTypeFilter && activeFilters.length === 0) return rawSpans;

    // Map filter field names to span property accessors
    function getSpanValue(span, field) {
      const attrs = span.span_attributes || {};
      switch (field) {
        case "traceName":
          return span.name;
        case "status":
          return span.status;
        case "model":
          return span.model;
        case "nodeType":
          return span.observation_type;
        case "provider":
          return span.provider;
        case "service_name":
          return span.name;
        case "userId":
          return span.user_id;
        default:
          // Check span attributes for custom fields
          return attrs[field] ?? span[field];
      }
    }

    function matchesSpan(entry) {
      const span = getSpan(entry);

      // Span type filter from Display panel
      if (hasTypeFilter) {
        const type = (span.observation_type || "").toLowerCase();
        if (!spanTypeFilter.some((t) => t.toLowerCase() === type)) return false;
      }

      // Span filters from Filter panel
      if (activeFilters.length === 0) return true;
      return activeFilters.every((f) => {
        const spanVal = getSpanValue(span, f.field);
        if (spanVal === undefined || spanVal === null) return false;

        const sVal = String(spanVal).toLowerCase();
        const matchValues = f.value.map((v) => String(v).toLowerCase());

        switch (f.operator) {
          case "is":
            return matchValues.some((v) => sVal === v);
          case "is_not":
            return matchValues.every((v) => sVal !== v);
          case "contains":
            return matchValues.some((v) => sVal.includes(v));
          case "not_contains":
            return matchValues.every((v) => !sVal.includes(v));
          default:
            return matchValues.some((v) => sVal === v);
        }
      });
    }

    function filterTree(entries) {
      if (!entries) return [];
      return entries
        .map((entry) => {
          const filteredChildren = filterTree(entry.children);
          const selfMatches = matchesSpan(entry);
          // Keep span if it matches OR any child matches (preserve tree structure)
          if (selfMatches || filteredChildren.length > 0) {
            return {
              ...entry,
              children: filteredChildren,
              _filterMatch: selfMatches,
            };
          }
          return null;
        })
        .filter(Boolean);
    }

    return filterTree(rawSpans);
  }, [rawSpans, spanFilters, spanTypeFilter]);

  // Auto-select root span when trace data loads (or when trace changes).
  // If caller passed ``initialSpanId`` (e.g. opened from span-grid), prefer
  // that so the drawer lands on the clicked span.
  const rootSpanId =
    rawSpans?.find((entry) => !getSpan(entry).parent_span_id)?.observation_span
      ?.id ?? null;
  useEffect(() => {
    setSelectedSpanId(initialSpanId || rootSpanId || null);
  }, [traceId, rootSpanId, initialSpanId]);

  // Find selected span in tree
  const selectedSpanData = useMemo(() => {
    if (!selectedSpanId || !spans) return null;
    function find(entries) {
      for (const entry of entries) {
        const span = getSpan(entry);
        if (span?.id === selectedSpanId) return entry;
        if (entry.children?.length) {
          const found = find(entry.children);
          if (found) return found;
        }
      }
      return null;
    }
    return find(spans);
  }, [selectedSpanId, spans]);

  const handleSelectSpan = useCallback((spanId) => {
    setSelectedSpanId((prev) => (prev === spanId ? null : spanId));
  }, []);

  const handleAction = useCallback(
    (actionId, anchorEl) => {
      if (actionId === "_open") {
        setActionsAnchorEl(anchorEl);
        return;
      }
      setActionsAnchorEl(null);

      const span = selectedSpanData ? getSpan(selectedSpanData) : null;

      switch (actionId) {
        case "queue":
          setQueueAnchorEl(actionsAnchorEl);
          break;
        case "annotate":
          setAnnotateDrawerOpen({
            spanId: span?.id || null,
            spanName: span?.name || null,
            observationType: span?.observation_type || null,
          });
          break;
        case "dataset":
          setDatasetDrawerOpen(true);
          break;
        case "workbench": {
          if (!span) {
            enqueueSnackbar("Select a span first", { variant: "warning" });
            break;
          }
          const promptTemplateId =
            span.prompt_template_id || span.promptTemplateId;
          if (promptTemplateId) {
            navigate(
              `/dashboard/workbench/create/${promptTemplateId}?tab=Playground`,
            );
          } else {
            const { messages, variableNames } = buildPromptConfigFromSpan(span);
            createPromptDraft({
              name: "",
              prompt_config: [{ messages }],
              ...(Object.keys(variableNames).length > 0 && {
                variable_names: variableNames,
              }),
            });
          }
          break;
        }
        case "playground":
          if (traceId) {
            createGraphFromTrace({ trace_id: traceId });
          }
          break;
        case "tags":
          setTagsAnchorEl(anchorEl || document.body);
          break;
        default:
          enqueueSnackbar(`${actionId} — coming soon`, { variant: "info" });
      }
    },
    [
      selectedSpanData,
      projectId,
      navigate,
      createPromptDraft,
      createGraphFromTrace,
      traceId,
      actionsAnchorEl,
    ],
  );

  // Resizable divider drag handler
  const handleDragStart = useCallback(
    (e) => {
      e.preventDefault();
      const startX = e.clientX;
      const startWidth = leftPanelWidth;
      const drawer = e.target.closest("[data-drawer-content]");
      if (!drawer) return;
      const drawerWidth = drawer.offsetWidth;

      const onMouseMove = (moveEvent) => {
        const diff = moveEvent.clientX - startX;
        const newPct = startWidth + (diff / drawerWidth) * 100;
        setLeftPanelWidth(Math.min(70, Math.max(20, newPct)));
      };
      const onMouseUp = () => {
        document.removeEventListener("mousemove", onMouseMove);
        document.removeEventListener("mouseup", onMouseUp);
      };
      document.addEventListener("mousemove", onMouseMove);
      document.addEventListener("mouseup", onMouseUp);
    },
    [leftPanelWidth],
  );

  const isImagineActive =
    activeDrawerTab === "__new_imagine__" ||
    drawerTabs.find((t) => t.id === activeDrawerTab)?.tabType === "imagine";

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      variant="persistent"
      PaperProps={{
        sx: {
          width: isFullscreen
            ? `calc(100vw - ${falconOffsetPx}px)`
            : `${drawerWidth}vw`,
          height: "100vh",
          position: "fixed",
          right: `${falconOffsetPx}px`,
          borderRadius: 0,
          bgcolor: "background.paper",
          display: "flex",
          flexDirection: "column",
          borderLeft: isFullscreen ? "none" : "1px solid",
          borderColor: "divider",
          transition: "none",
        },
      }}
      ModalProps={{
        BackdropProps: { style: { backgroundColor: "transparent" } },
      }}
    >
      {/* Resize handle — left edge */}
      {!isFullscreen && (
        <Box
          onMouseDown={(e) => {
            e.preventDefault();
            const startX = e.clientX;
            const startWidth = drawerWidth;
            const onMove = (moveE) => {
              const diff = startX - moveE.clientX;
              const newWidth = startWidth + (diff / window.innerWidth) * 100;
              setDrawerWidth(Math.min(95, Math.max(30, newWidth)));
            };
            const onUp = () => {
              document.removeEventListener("mousemove", onMove);
              document.removeEventListener("mouseup", onUp);
              document.body.style.cursor = "";
              document.body.style.userSelect = "";
            };
            document.body.style.cursor = "col-resize";
            document.body.style.userSelect = "none";
            document.addEventListener("mousemove", onMove);
            document.addEventListener("mouseup", onUp);
          }}
          sx={{
            position: "absolute",
            left: 0,
            top: 0,
            bottom: 0,
            width: 4,
            cursor: "col-resize",
            zIndex: 10,
            "&:hover": { bgcolor: "primary.main", opacity: 0.3 },
          }}
        />
      )}

      {/* Header */}
      <DrawerHeader
        traceId={traceId}
        projectId={projectId}
        onClose={onClose}
        onPrev={onPrev}
        onNext={onNext}
        hasPrev={hasPrev}
        hasNext={hasNext}
        onFullscreen={
          initialFullscreen ? undefined : () => setIsFullscreen((prev) => !prev)
        }
        isFullscreen={isFullscreen}
        onOpenNewTab={
          initialFullscreen
            ? undefined
            : () => {
                if (traceId && projectId) {
                  window.open(
                    `/dashboard/observe/${projectId}/trace/${traceId}`,
                    "_blank",
                  );
                }
              }
        }
        onDownload={() => {
          // Download raw trace data as JSON
          if (data) {
            const blob = new Blob([JSON.stringify(data, null, 2)], {
              type: "application/json",
            });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `trace-${traceId || "unknown"}.json`;
            a.click();
            URL.revokeObjectURL(url);
            enqueueSnackbar("Trace downloaded", { variant: "success" });
          }
        }}
        onShare={() => setShareDialogOpen(true)}
      />

      {/* Toolbar — tab bar + filter/display/save buttons */}
      <DrawerToolbar
        tabs={drawerTabs}
        activeTabId={activeDrawerTab}
        onTabChange={handleTabChange}
        onCloseTab={handleCloseTab}
        onCreateTab={handleCreateView}
        onCreateImagineTab={handleCreateImagineTab}
        onReorderTabs={(orderedIds) => {
          // orderedIds are the new order for imagine tabs only. Merge with the
          // other saved views (non-imagine) so their positions are preserved.
          const imagineIdSet = new Set(orderedIds);
          const imaginePosById = Object.fromEntries(
            orderedIds.map((id, i) => [id, i]),
          );
          const allViews = customViews;
          const nonImagine = allViews.filter((v) => !imagineIdSet.has(v.id));
          const merged = [
            ...nonImagine.map((v, i) => ({ id: v.id, position: i })),
            ...orderedIds.map((id) => ({
              id,
              position: nonImagine.length + imaginePosById[id],
            })),
          ];
          reorderSavedViews({ project_id: projectId, order: merged });
        }}
        onFilterOpen={(e) => setFilterAnchorEl(e?.currentTarget || e)}
        onDisplayOpen={(el) => setDisplayAnchorEl(el)}
        hasActiveFilter={spanFilters.length > 0}
      />

      {/* Display options popover — hidden on Imagine tab */}
      {!isImagineActive && (
        <TraceDisplayPanel
          anchorEl={displayAnchorEl}
          open={Boolean(displayAnchorEl)}
          onClose={() => setDisplayAnchorEl(null)}
          viewMode={viewMode}
          onViewModeChange={setViewMode}
          spanTypeFilter={spanTypeFilter}
          onSpanTypeFilterChange={setSpanTypeFilter}
          visibleMetrics={visibleMetrics}
          onToggleMetric={(metric) =>
            setVisibleMetrics((prev) => ({ ...prev, [metric]: !prev[metric] }))
          }
          showAgentGraph={showAgentGraph}
          onToggleAgentGraph={() => setShowAgentGraph((prev) => !prev)}
          onResetView={handleResetView}
          onSetDefaultView={handleSetDefaultView}
        />
      )}

      {/* Span filter popover — hidden on Imagine tab */}
      {!isImagineActive && (
        <TraceFilterPanel
          anchorEl={filterAnchorEl}
          open={Boolean(filterAnchorEl)}
          onClose={() => setFilterAnchorEl(null)}
          currentFilters={spanFilters}
          onApply={(filters) => setSpanFilters(filters || [])}
          projectId={projectId}
        />
      )}

      {/* Active filter chips row — hidden on Imagine tab */}
      {!isImagineActive && spanFilters.length > 0 && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            px: 1.5,
            py: 0.5,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
            bgcolor: "background.default",
            gap: 0.5,
            minHeight: 32,
          }}
        >
          {/* Filter chips */}
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.5,
              flex: 1,
              flexWrap: "wrap",
              overflow: "hidden",
            }}
          >
            {spanFilters.map((f, idx) => (
              <Box
                key={idx}
                sx={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "4px",
                  px: 0.75,
                  py: 0.25,
                  bgcolor: "background.paper",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "4px",
                  fontSize: 11,
                  color: "text.primary",
                  whiteSpace: "nowrap",
                }}
              >
                <Iconify
                  icon="mdi:filter-variant"
                  width={12}
                  color="text.disabled"
                />
                <span style={{ fontWeight: 500 }}>{f.field}</span>
                <span style={{ color: "var(--text-secondary)" }}>
                  {f.operator || "is"}
                </span>
                <span style={{ fontWeight: 600 }}>
                  {Array.isArray(f.value) ? f.value.join(", ") : f.value}
                </span>
                <Iconify
                  icon="mdi:close"
                  width={12}
                  sx={{
                    cursor: "pointer",
                    color: "text.disabled",
                    "&:hover": { color: "text.primary" },
                  }}
                  onClick={() => {
                    setSpanFilters((prev) => {
                      const next = prev.filter((_, i) => i !== idx);
                      return next;
                    });
                  }}
                />
              </Box>
            ))}

            {/* + button to add more filters */}
            <Box
              onClick={(e) => setFilterAnchorEl(e.currentTarget)}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                width: 22,
                height: 22,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "4px",
                cursor: "pointer",
                color: "text.disabled",
                "&:hover": { bgcolor: "action.hover", color: "text.secondary" },
              }}
            >
              <Iconify icon="mdi:plus" width={14} />
            </Box>
          </Box>

          {/* Clear + Save */}
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
              flexShrink: 0,
              ml: 1,
            }}
          >
            <Typography
              onClick={() => setSpanFilters([])}
              sx={{
                fontSize: 12,
                color: "text.secondary",
                cursor: "pointer",
                fontWeight: 500,
                "&:hover": { color: "text.primary" },
              }}
            >
              Clear
            </Typography>
            <Typography
              onClick={() =>
                enqueueSnackbar("View saved", { variant: "success" })
              }
              sx={{
                fontSize: 12,
                color: "text.secondary",
                cursor: "pointer",
                fontWeight: 500,
                "&:hover": { color: "text.primary" },
              }}
            >
              Save
            </Typography>
          </Box>
        </Box>
      )}

      {/* Main content area */}
      <Box
        data-drawer-content
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: isImagineActive
            ? "column"
            : viewMode === "timeline"
              ? "column"
              : "row",
          overflow: "hidden",
        }}
      >
        {isImagineActive ? (
          <ImagineTab
            traceId={traceId}
            projectId={projectId}
            traceData={{ spans: rawSpans, summary, graph }}
            readOnly={activeDrawerTab !== "__new_imagine__"}
            savedViewId={
              activeDrawerTab !== "__new_imagine__" ? activeDrawerTab : null
            }
            savedWidgets={
              drawerTabs.find((t) => t.id === activeDrawerTab)?.config?.widgets
            }
            savedConversationId={
              drawerTabs.find((t) => t.id === activeDrawerTab)?.config
                ?.conversation_id ||
              drawerTabs.find((t) => t.id === activeDrawerTab)?.config
                ?.conversationId
            }
            onSaved={() =>
              queryClient.invalidateQueries({
                queryKey: ["saved-views", projectId],
              })
            }
          />
        ) : isLoading ? (
          <Box
            sx={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <CircularProgress size={32} />
          </Box>
        ) : viewMode === "timeline" ? (
          /* ── Timeline layout: full-width timeline on top, detail panel on bottom ── */
          <>
            {/* Top: Timeline (full width) */}
            <Box
              sx={{
                flex: selectedSpanData ? "0 0 55%" : 1,
                overflow: "auto",
                borderBottom: selectedSpanData ? "1px solid" : "none",
                borderColor: "divider",
              }}
            >
              <SpanTreeTimeline
                spans={spans}
                selectedSpanId={selectedSpanId}
                onSelectSpan={handleSelectSpan}
              />
            </Box>

            {/* Bottom: Span detail (appears when span clicked) */}
            {selectedSpanData && (
              <Box
                sx={{
                  flex: 1,
                  overflow: "auto",
                  display: "flex",
                  flexDirection: "column",
                  minHeight: 0,
                }}
              >
                <SpanDetailPane
                  entry={selectedSpanData}
                  allSpans={spans}
                  traceStartTime={
                    spans?.length ? getSpan(spans[0])?.start_time : null
                  }
                  isRootSpan={selectedSpanId === rootSpanId}
                  traceTags={data?.trace?.tags || []}
                  projectId={projectId}
                  onClose={() => setSelectedSpanId(null)}
                  onAction={handleAction}
                  onSelectSpan={handleSelectSpan}
                  drawerOpen={open}
                />
              </Box>
            )}
          </>
        ) : (
          /* ── Tree layout: left/right split ── */
          <>
            {/* Left Panel: Tree + Agent Graph with resizable split */}
            <LeftPanelSplit
              leftPanelWidth={leftPanelWidth}
              viewMode={viewMode}
              spans={spans}
              selectedSpanId={selectedSpanId}
              onSelectSpan={handleSelectSpan}
              visibleMetrics={visibleMetrics}
              setVisibleMetrics={setVisibleMetrics}
              showAgentGraph={showAgentGraph}
              onRefresh={handleRefresh}
            />

            {/* Resizable divider with visual handle */}
            <Box
              onMouseDown={handleDragStart}
              sx={{
                width: 8,
                cursor: "col-resize",
                bgcolor: "transparent",
                flexShrink: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                "&:hover": { "& .divider-dots": { opacity: 1 } },
                "&:active": { "& .divider-dots": { opacity: 1 } },
                transition: "background-color 150ms",
              }}
            >
              <Box
                className="divider-dots"
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "3px",
                  opacity: 0.4,
                  transition: "opacity 150ms",
                }}
              >
                {[0, 1, 2, 3, 4, 5].map((i) => (
                  <Box
                    key={i}
                    sx={{
                      width: 3,
                      height: 3,
                      borderRadius: "50%",
                      bgcolor: "text.disabled",
                    }}
                  />
                ))}
              </Box>
            </Box>

            {/* Right Panel: Span detail */}
            <Box
              sx={{
                flex: 1,
                overflow: "auto",
                display: "flex",
                flexDirection: "column",
              }}
            >
              {selectedSpanData ? (
                <SpanDetailPane
                  entry={selectedSpanData}
                  allSpans={spans}
                  traceStartTime={
                    spans?.length ? getSpan(spans[0])?.start_time : null
                  }
                  isRootSpan={selectedSpanId === rootSpanId}
                  traceTags={data?.trace?.tags || []}
                  projectId={projectId}
                  onClose={() => setSelectedSpanId(null)}
                  onAction={handleAction}
                  onSelectSpan={handleSelectSpan}
                  drawerOpen={open}
                />
              ) : (
                /* Summary when no span selected */
                <Box
                  sx={{
                    p: 3,
                    textAlign: "center",
                    color: "text.secondary",
                    mt: 8,
                  }}
                >
                  <Iconify
                    icon="mdi:cursor-default-click-outline"
                    width={40}
                    sx={{ mb: 1, opacity: 0.5 }}
                  />
                  <Typography variant="body2" fontSize={13}>
                    Select a span to view details
                  </Typography>
                  {summary && (
                    <Box
                      sx={{
                        mt: 2,
                        display: "flex",
                        justifyContent: "center",
                        gap: 3,
                      }}
                    >
                      <Typography variant="caption">
                        {summary.totalSpans || summary.total_spans} spans
                      </Typography>
                      <Typography variant="caption">
                        {formatLatency(
                          summary.totalDurationMs || summary.total_duration_ms,
                        )}
                      </Typography>
                      <Typography variant="caption">
                        {formatTokenCount(summary.total_tokens)} tokens
                      </Typography>
                      <Typography variant="caption">
                        {formatCost(summary.total_cost)}
                      </Typography>
                    </Box>
                  )}
                </Box>
              )}
            </Box>
          </>
        )}
      </Box>

      {/* Actions context menu */}
      <Menu
        anchorEl={actionsAnchorEl}
        open={Boolean(actionsAnchorEl)}
        onClose={() => setActionsAnchorEl(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: {
              minWidth: 220,
              boxShadow: "0 4px 16px rgba(0,0,0,0.12)",
              borderRadius: "6px",
              "& .MuiMenuItem-root": { fontSize: 13, gap: 1, py: 0.75 },
            },
          },
        }}
      >
        {ACTION_ITEMS.filter((item) => {
          if (!item.spanTypes) return true;
          const span = selectedSpanData ? getSpan(selectedSpanData) : null;
          const type = (span?.observation_type || "").toLowerCase();
          return item.spanTypes.includes(type);
        }).map((item) => (
          <MenuItem
            key={item.id}
            onClick={(e) => handleAction(item.id, e.currentTarget)}
            disabled={
              (item.id === "workbench" && isCreatingDraft) ||
              (item.id === "playground" && isCreatingGraph)
            }
          >
            <Iconify icon={item.icon} width={16} />
            {item.id === "workbench" && isCreatingDraft
              ? "Creating prompt..."
              : item.id === "playground" && isCreatingGraph
                ? "Creating agent graph..."
                : item.label}
          </MenuItem>
        ))}
      </Menu>

      {/* Share Dialog */}
      {traceId && (
        <ShareDialog
          open={shareDialogOpen}
          onClose={() => setShareDialogOpen(false)}
          resourceType="trace"
          resourceId={traceId}
        />
      )}

      {/* Add to Annotation Queue Dialog */}
      <AddToQueueDialog
        anchorEl={queueAnchorEl}
        onClose={() => setQueueAnchorEl(null)}
        sourceType={selectedSpanId ? "observation_span" : "trace"}
        sourceIds={selectedSpanId ? [selectedSpanId] : traceId ? [traceId] : []}
        itemName={selectedSpanData ? getSpan(selectedSpanData)?.name : "Trace"}
      />

      {/* Move to Dataset Drawer */}
      <AddDataset
        handleClose={() => setDatasetDrawerOpen(false)}
        actionToDataset={datasetDrawerOpen}
        spanId={selectedSpanId}
      />

      {/* Annotate Drawer — new sidebar UI */}
      <Drawer
        anchor="right"
        open={Boolean(annotateDrawerOpen)}
        onClose={() => setAnnotateDrawerOpen(null)}
        variant="temporary"
        PaperProps={{
          sx: {
            width: 360,
            height: "100vh",
            borderRadius: 0,
            bgcolor: "background.paper",
          },
        }}
        ModalProps={{
          BackdropProps: { style: { backgroundColor: "rgba(0,0,0,0.1)" } },
        }}
      >
        <AnnotationSidebarContent
          sources={[
            {
              sourceType: "observation_span",
              sourceId: annotateDrawerOpen?.spanId || rootSpanId,
            },
          ]}
          onClose={() => setAnnotateDrawerOpen(null)}
          onAddLabel={() => setAddLabelDrawerOpen(true)}
          onScoresChanged={() => {
            queryClient.invalidateQueries({
              queryKey: ["trace-detail", traceId],
            });
            if (annotateDrawerOpen?.spanId) {
              queryClient.invalidateQueries({
                queryKey: ["span-annotation", annotateDrawerOpen.spanId],
              });
            }
            queryClient.invalidateQueries({
              queryKey: ["annotation-queues", "for-source"],
            });
            // The trace grid uses AG Grid's server-side row model with its
            // own row cache — invalidating React Query keys is not enough to
            // refresh the annotation column shown for this trace's row.
            // Ask the parent grid to refresh so the new annotation surfaces
            // without a full page reload.
            refreshParentGrid?.();
          }}
          showHeader
        />
      </Drawer>

      {/* Add Label Drawer — opens from AnnotationSidebarContent "+ Add Label" */}
      <AddLabelDrawer
        open={addLabelDrawerOpen}
        onClose={() => setAddLabelDrawerOpen(false)}
        projectId={projectId}
        onLabelsChanged={() => {
          queryClient.invalidateQueries({
            queryKey: ["trace-detail", traceId],
          });
          queryClient.invalidateQueries({
            queryKey: ["annotation-queues", "for-source"],
          });
        }}
      />

      {/* Add Tags Popover */}
      <AddTagsPopover
        anchorEl={tagsAnchorEl}
        open={Boolean(tagsAnchorEl)}
        onClose={() => setTagsAnchorEl(null)}
        traceId={traceId}
        spanId={selectedSpanId}
        currentTags={
          selectedSpanData
            ? getSpan(selectedSpanData)?.tags || []
            : data?.trace?.tags || []
        }
      />

      {/* Save View Popover */}
      <SaveViewPopover
        anchorEl={saveViewAnchor}
        open={Boolean(saveViewAnchor)}
        onClose={() => setSaveViewAnchor(null)}
        onSave={handleSaveViewConfirm}
        isLoading={isSavingView}
      />

      {/* Delete tab confirmation dialog */}
      <ConfirmDialog
        open={Boolean(deleteTabId)}
        onClose={() => setDeleteTabId(null)}
        title="Delete view"
        content={`Are you sure you want to delete "${drawerTabs.find((t) => t.id === deleteTabId)?.label || "this view"}"? This action cannot be undone.`}
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={() => {
              const tabId = deleteTabId;
              setDeleteTabId(null);
              deleteSavedView(tabId, {
                onSuccess: () => {
                  if (activeDrawerTab === tabId) setActiveDrawerTab("trace");
                  enqueueSnackbar("View deleted", { variant: "info" });
                },
                onError: () => {
                  enqueueSnackbar("Failed to delete view", {
                    variant: "error",
                  });
                },
              });
            }}
          >
            Delete
          </Button>
        }
      />
    </Drawer>
  );
};

TraceDetailDrawerV2.propTypes = {
  traceId: PropTypes.string,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  projectId: PropTypes.string,
  onPrev: PropTypes.func,
  onNext: PropTypes.func,
  hasPrev: PropTypes.bool,
  hasNext: PropTypes.bool,
  initialFullscreen: PropTypes.bool,
  initialSpanId: PropTypes.string,
  refreshParentGrid: PropTypes.func,
};

export default React.memo(TraceDetailDrawerV2);

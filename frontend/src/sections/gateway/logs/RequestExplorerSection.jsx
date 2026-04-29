import React, { useState, useEffect, useCallback, useMemo } from "react";
import {
  Box,
  Stack,
  Button,
  Tabs,
  Tab,
  TextField,
  Badge,
  InputAdornment,
  Chip,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SectionHeader from "../components/SectionHeader";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";
import axiosInstance, { endpoints } from "src/utils/axios";
import useFilters from "./hooks/useFilters";
import RequestTable from "./RequestTable";
import RequestDetailDrawer from "./RequestDetailDrawer";
import FilterPanel from "./FilterPanel";
import SessionExplorer from "./SessionExplorer";

// ---------------------------------------------------------------------------
// Quick filter definitions
// ---------------------------------------------------------------------------
const QUICK_FILTERS = [
  { label: "All", key: "all" },
  { label: "Errors", key: "errors", params: { isError: "true" } },
  { label: "Slow (>1s latency)", key: "slow", params: { minLatency: "1000" } },
  { label: "Cache Hits", key: "cache", params: { cache_hit: "true" } },
  {
    label: "Guardrails",
    key: "guardrails",
    params: { guardrailTriggered: "true" },
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Return the key of the currently-active quick filter, or "all". */
function activeQuickFilter(filters) {
  if (filters.isError === "true") return "errors";
  if (filters.minLatency === "1000") return "slow";
  if (filters.cacheHit === "true") return "cache";
  if (filters.guardrailTriggered === "true") return "guardrails";
  return "all";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const RequestExplorerSection = () => {
  // --- URL-synced filters ---------------------------------------------------
  const { filters, setFilter, setFilters, clearFilters, activeFilterCount } =
    useFilters();

  // --- Local UI state -------------------------------------------------------
  const [selectedLogId, setSelectedLogId] = useState(null);
  const [filterPanelOpen, setFilterPanelOpen] = useState(false);
  const [searchValue, setSearchValue] = useState(filters.search || "");
  const [exportAnchor, setExportAnchor] = useState(null);
  const [isExporting, setIsExporting] = useState(false);

  // --- Debounced search -----------------------------------------------------
  useEffect(() => {
    const timer = setTimeout(() => {
      if (searchValue !== (filters.search || "")) {
        setFilter("search", searchValue || undefined);
      }
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchValue]);

  // Keep the controlled input in sync when the URL changes externally
  // (e.g. browser back/forward or clearing filters).
  useEffect(() => {
    setSearchValue(filters.search || "");
  }, [filters.search]);

  // --- View tab -------------------------------------------------------------
  const currentView = filters.view || "requests";

  const handleViewChange = useCallback(
    (_event, newValue) => {
      setFilter("view", newValue === "requests" ? undefined : newValue);
    },
    [setFilter],
  );

  // --- Quick filters --------------------------------------------------------
  const activeQF = useMemo(() => activeQuickFilter(filters), [filters]);

  const handleQuickFilter = useCallback(
    (qf) => {
      if (qf.key === "all") {
        clearFilters();
        return;
      }
      // Clear conflicting boolean/range params, then apply this quick filter
      const cleaned = { view: filters.view, search: filters.search };
      Object.entries(qf.params).forEach(([k, v]) => {
        cleaned[k] = v;
      });
      setFilters(cleaned);
    },
    [filters.view, filters.search, clearFilters, setFilters],
  );

  // --- Export ---------------------------------------------------------------
  const handleExportOpen = (event) => setExportAnchor(event.currentTarget);
  const handleExportClose = () => setExportAnchor(null);

  const handleExport = useCallback(
    async (format) => {
      setExportAnchor(null);
      setIsExporting(true);
      try {
        const params = {};
        if (filters.search) params.search = filters.search;
        if (filters.model) params.model = filters.model;
        if (filters.provider) params.provider = filters.provider;
        if (filters.startedAfter) params.started_after = filters.startedAfter;
        if (filters.startedBefore)
          params.started_before = filters.startedBefore;
        if (filters.isError) params.is_error = filters.isError;
        if (filters.cacheHit) params.cache_hit = filters.cacheHit;
        if (filters.guardrailTriggered)
          params.guardrail_triggered = filters.guardrailTriggered;
        if (filters.minLatency) params.min_latency = filters.minLatency;
        if (filters.maxLatency) params.max_latency = filters.maxLatency;
        if (filters.minCost) params.min_cost = filters.minCost;
        if (filters.maxCost) params.max_cost = filters.maxCost;
        if (filters.userId) params.user_id = filters.userId;
        if (filters.sessionId) params.session_id = filters.sessionId;
        if (filters.gatewayId) params.gateway_id = filters.gatewayId;
        if (filters.apiKeyId) params.api_key_id = filters.apiKeyId;
        if (filters.requestId) params.request_id = filters.requestId;
        if (filters.isStream) params.is_stream = filters.isStream;
        if (filters.minTokens) params.min_tokens = filters.minTokens;
        if (filters.maxTokens) params.max_tokens = filters.maxTokens;
        if (filters.fallbackUsed) params.fallback_used = filters.fallbackUsed;
        if (filters.sort) params.ordering = filters.sort;
        if (filters.statusCode) params.status_code = filters.statusCode;

        params.export_format = format;

        const response = await axiosInstance.get(
          endpoints.gateway.requestLogExport,
          { params, responseType: "blob" },
        );

        const dateStr = new Date().toISOString().split("T")[0];
        const filename = `agentcc-logs-${dateStr}.${format}`;
        const blobUrl = URL.createObjectURL(response.data);
        const link = document.createElement("a");
        link.href = blobUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(blobUrl);
      } catch {
        // Silently handle -- could add a snackbar notification in the future
      } finally {
        setIsExporting(false);
      }
    },
    [filters],
  );

  // --- Filter panel apply ---------------------------------------------------
  const handleApplyFilters = useCallback(
    (newFilters) => {
      setFilters(newFilters);
      setFilterPanelOpen(false);
    },
    [setFilters],
  );

  // =========================================================================
  // Render
  // =========================================================================
  return (
    <Box p={3}>
      {/* ---- Header ---- */}
      <SectionHeader
        icon={GATEWAY_ICONS.logs}
        title="Request Logs"
        subtitle="Search and inspect individual gateway requests"
      >
        <Button
          variant="outlined"
          size="small"
          startIcon={<Iconify icon="mdi:download-outline" width={20} />}
          onClick={handleExportOpen}
          disabled={isExporting}
        >
          {isExporting ? "Exporting..." : "Export"}
        </Button>

        <Menu
          anchorEl={exportAnchor}
          open={Boolean(exportAnchor)}
          onClose={handleExportClose}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
        >
          <MenuItem onClick={() => handleExport("csv")}>
            <ListItemIcon>
              <Iconify icon="mdi:file-document-outline" width={18} />
            </ListItemIcon>
            <ListItemText>Export CSV</ListItemText>
          </MenuItem>
          <MenuItem onClick={() => handleExport("json")}>
            <ListItemIcon>
              <Iconify icon="mdi:code-json" width={18} />
            </ListItemIcon>
            <ListItemText>Export JSON</ListItemText>
          </MenuItem>
        </Menu>
      </SectionHeader>

      {/* ---- Search + Filters button ---- */}
      <Stack direction="row" spacing={2} alignItems="center" mb={2}>
        <TextField
          placeholder="Search model, provider, request ID..."
          size="small"
          fullWidth
          value={searchValue}
          onChange={(e) => setSearchValue(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="eva:search-outline" width={18} />
              </InputAdornment>
            ),
          }}
        />
        <Badge badgeContent={activeFilterCount} color="primary">
          <Button
            variant="outlined"
            startIcon={<Iconify icon="mdi:filter-outline" width={20} />}
            onClick={() => setFilterPanelOpen(true)}
            sx={{ whiteSpace: "nowrap" }}
          >
            Filters
          </Button>
        </Badge>
      </Stack>

      {/* ---- Quick filter chips ---- */}
      <Stack
        direction="row"
        spacing={1}
        mb={2}
        sx={{ overflowX: "auto", pb: 0.5 }}
      >
        {QUICK_FILTERS.map((qf) => (
          <Chip
            key={qf.key}
            label={qf.label}
            size="medium"
            variant={activeQF === qf.key ? "filled" : "outlined"}
            color={activeQF === qf.key ? "primary" : "default"}
            onClick={() => handleQuickFilter(qf)}
          />
        ))}
      </Stack>

      {/* ---- Tabs: Requests | Sessions ---- */}
      <Tabs
        value={currentView}
        onChange={handleViewChange}
        variant="standard"
        sx={{ mb: 2 }}
      >
        <Tab label="Requests" value="requests" />
        <Tab label="Sessions" value="sessions" />
      </Tabs>

      {/* ---- Main content ---- */}
      {currentView === "requests" ? (
        <RequestTable
          filters={filters}
          setFilter={setFilter}
          setFilters={setFilters}
          onSelectLog={(logId) => setSelectedLogId(logId)}
        />
      ) : (
        <SessionExplorer
          filters={filters}
          onRequestClick={(logId) => setSelectedLogId(logId)}
        />
      )}

      {/* ---- Detail drawer ---- */}
      <RequestDetailDrawer
        logId={selectedLogId}
        open={Boolean(selectedLogId)}
        onClose={() => setSelectedLogId(null)}
      />

      {/* ---- Filter panel drawer ---- */}
      <FilterPanel
        open={filterPanelOpen}
        onClose={() => setFilterPanelOpen(false)}
        filters={filters}
        onApply={handleApplyFilters}
      />
    </Box>
  );
};

export default RequestExplorerSection;

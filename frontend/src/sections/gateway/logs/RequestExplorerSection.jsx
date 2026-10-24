import React, { useState, useEffect, useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Alert,
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
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Typography,
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
import useRequestColumns from "./columns/useRequestColumns";
import ColumnsPickerPopover from "./columns/ColumnsPickerPopover";
import { COLUMNS_POPOVER_ID } from "./columns/columnModel";
import { requestLogQueryParams } from "./hooks/useRequestLogs";

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
// Export
// ---------------------------------------------------------------------------

// The most rows one export holds: futureagi/agentcc/services/export.py
// MAX_EXPORT_ROWS. Above it the server streams the first EXPORT_ROW_CAP rows
// in the table's order when asked (max_rows), and the dialog says so first.
const EXPORT_ROW_CAP = 10000;

// URL keys that shape the table without filtering it.
const NON_FILTER_KEYS = new Set(["view", "page", "pageSize", "sort"]);

function hasFilters(filters) {
  return Object.entries(filters).some(([key, value]) => {
    if (NON_FILTER_KEYS.has(key) || value === undefined || value === "") {
      return false;
    }
    return key === "search" ? String(value).trim().length >= 2 : true;
  });
}

const formatCount = (n) => Number(n).toLocaleString("en-US");

function countSentence(count) {
  if (count.state === "loading") return "Counting the matching requests...";
  if (count.state === "error") {
    return `The matching requests could not be counted (${count.message}). The download stops at ${formatCount(EXPORT_ROW_CAP)} rows.`;
  }
  if (count.n === 0) {
    return "No requests match these filters, so there is nothing to export.";
  }
  if (count.n > EXPORT_ROW_CAP) {
    return `${formatCount(count.n)} requests match. An export holds at most ${formatCount(EXPORT_ROW_CAP)} rows, so this one holds the first ${formatCount(EXPORT_ROW_CAP)} in the table's order. Narrow the filters (a date range, for example) to export the rest.`;
  }
  return count.n === 1
    ? "1 request will be exported."
    : `${formatCount(count.n)} requests will be exported.`;
}

function blobText(blob) {
  if (typeof blob === "string") return Promise.resolve(blob);
  if (typeof blob?.text === "function") return blob.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

// A refused export's body is a Blob (responseType "blob"): read its message.
async function readBlobMessage(blob, fallback) {
  try {
    const body = JSON.parse(await blobText(blob));
    const message =
      body?.message ||
      body?.detail ||
      (typeof body?.result === "string" ? body.result : "");
    return message || fallback;
  } catch {
    return fallback;
  }
}

function saveBlob(blob, format) {
  const dateStr = new Date().toISOString().split("T")[0];
  const blobUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = blobUrl;
  link.download = `agentcc-logs-${dateStr}.${format}`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(blobUrl);
}

// Counts what the table's query matches, states the scope and the cap, then
// downloads exactly the table's query (its filters, search and order).
const ExportDialog = ({ format, filters, onClose }) => {
  const open = Boolean(format);
  const params = useMemo(() => requestLogQueryParams(filters), [filters]);
  const [count, setCount] = useState({ state: "loading" });
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    setCount({ state: "loading" });
    setError("");
    axiosInstance
      .get(endpoints.gateway.requestLogs, {
        params: { ...params, page: 1, limit: 1 },
      })
      .then((res) => {
        if (cancelled) return;
        const n = Number(res?.data?.result?.count ?? res?.data?.count);
        setCount(
          Number.isFinite(n)
            ? { state: "ok", n }
            : { state: "error", message: "the server sent no count" },
        );
      })
      .catch((err) => {
        if (cancelled) return;
        setCount({
          state: "error",
          message: err?.message || "the request failed",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [open, params]);

  const handleDownload = async () => {
    setDownloading(true);
    setError("");
    try {
      const response = await axiosInstance.get(
        endpoints.gateway.requestLogExport,
        {
          params: {
            ...params,
            export_format: format,
            max_rows: EXPORT_ROW_CAP,
          },
          responseType: "blob",
          // A refusal (400) resolves, so its body (a Blob) can be read; a
          // 401 still goes through the session refresh, a 5xx through the
          // app's error path.
          validateStatus: (status) => status !== 401 && status < 500,
        },
      );
      if (response.status >= 400) {
        setError(
          await readBlobMessage(
            response.data,
            `The export was refused (HTTP ${response.status}).`,
          ),
        );
        return;
      }
      saveBlob(response.data, format);
      onClose();
    } catch (err) {
      setError(err?.message || "The export failed.");
    } finally {
      setDownloading(false);
    }
  };

  const filtered = hasFilters(filters);
  const order =
    (filters.sort || "-started_at") === "-started_at"
      ? "newest first"
      : "in the table's order";
  const nothingToExport = count.state === "ok" && count.n === 0;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Export {String(format || "").toUpperCase()}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5}>
          <Typography variant="body2">
            {filtered
              ? "The export holds the requests that match the table's filters and search, in the table's order."
              : `No filters are set: the export holds every request the table lists, ${order}.`}
          </Typography>
          <Typography variant="body2" fontWeight={500}>
            {countSentence(count)}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Times in the file are in UTC.
          </Typography>
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleDownload}
          disabled={count.state === "loading" || nothingToExport || downloading}
        >
          {downloading ? "Downloading..." : "Download"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

ExportDialog.propTypes = {
  format: PropTypes.oneOf(["csv", "json", null]),
  filters: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
};

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
  const [exportFormat, setExportFormat] = useState(null);
  const [columnsAnchor, setColumnsAnchor] = useState(null);

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

  // --- Configurable columns --------------------------------------
  const requestColumns = useRequestColumns({
    enabled: currentView === "requests",
  });

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

  const handleExport = useCallback((format) => {
    setExportAnchor(null);
    setExportFormat(format);
  }, []);

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
        >
          Export
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
        {currentView === "requests" && (
          <Button
            variant="outlined"
            startIcon={<Iconify icon="mdi:view-column-outline" width={20} />}
            onClick={(event) => setColumnsAnchor(event.currentTarget)}
            // Edits need the user/org storage key to be saved.
            disabled={!requestColumns.storageKey}
            aria-haspopup="dialog"
            aria-expanded={Boolean(columnsAnchor)}
            aria-controls={columnsAnchor ? COLUMNS_POPOVER_ID : undefined}
            sx={{ whiteSpace: "nowrap" }}
          >
            Columns
          </Button>
        )}
      </Stack>

      {currentView === "requests" && (
        <ColumnsPickerPopover
          anchorEl={columnsAnchor}
          open={Boolean(columnsAnchor)}
          onClose={() => setColumnsAnchor(null)}
          entries={requestColumns.pickerEntries}
          stale={requestColumns.stale}
          visibleCount={requestColumns.visibleCount}
          totalCount={requestColumns.totalCount}
          atCap={requestColumns.atCap}
          declarationStatus={requestColumns.declarationStatus}
          declarationsAvailable={requestColumns.declarationsAvailable}
          declarationsFetching={requestColumns.declarationsFetching}
          reservedDeclared={requestColumns.reservedDeclared}
          unavailableCustomCount={requestColumns.unavailableCustomCount}
          onToggle={requestColumns.toggle}
          onMove={requestColumns.move}
          onRemove={requestColumns.remove}
          onReset={requestColumns.reset}
          onRetryDeclarations={requestColumns.retryDeclarations}
        />
      )}

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
          onSelectLog={setSelectedLogId}
          columns={requestColumns.columns}
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

      {/* ---- Export dialog ---- */}
      <ExportDialog
        format={exportFormat}
        filters={filters}
        onClose={() => setExportFormat(null)}
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

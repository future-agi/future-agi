import React, {
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { Navigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import Alert from "@mui/material/Alert";
import Badge from "@mui/material/Badge";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Divider from "@mui/material/Divider";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import ListItemText from "@mui/material/ListItemText";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import { paths } from "src/routes/paths";
import axiosInstance, { endpoints } from "src/utils/axios";

import ConfirmDialog from "../components/ConfirmDialog";
import { GatewayFilterBar, GatewayPageFrame } from "../components/frame";
import { GatewayIcon } from "../shell/gateway-icons";
import useFilters from "./hooks/useFilters";
import RequestTable from "./RequestTable";
import RequestVolumeStrip from "./RequestVolumeStrip";
import RequestDetailDrawer from "./RequestDetailDrawer";
import FilterPanel from "./FilterPanel";
import useRequestColumns from "./columns/useRequestColumns";
import ColumnsPickerPopover from "./columns/ColumnsPickerPopover";
import { COLUMNS_POPOVER_ID } from "./columns/columnModel";
import {
  QUICK_FILTERS,
  activeQuickFilter,
  appliedFilterChips,
  hiddenQuickFilters,
  withQuickFilter,
} from "./filterChips";
import { GatewayContext } from "../context/GatewayContext";
import { useGatewayConfig } from "../providers/hooks/useGatewayConfig";
import {
  EXPORT_ROW_CAP,
  downloadLogExport,
  exportErrorMessage,
} from "./exportLogs";
import { requestLogQueryParams } from "./hooks/useRequestLogs";
import { useManagedModelsStatus } from "../models/hooks/useManagedModels";
import { isManagedMode } from "../utils/providerLabel";
import { localZoneName } from "./logDates";
import {
  cleanQuery,
  deleteSavedView,
  loadSavedViews,
  saveView,
} from "./savedViews";

// The Logs page: every request through the gateway, newest first, in one
// dense grid. Filters live in the URL (so a filtered view is a link), the
// open request too (?log=<id>). Auto-refresh polls the newest page; there is
// no streaming endpoint, so it is named for what it does.

const AUTO_REFRESH_KEY = "gateway-logs:auto-refresh";
const AUTO_REFRESH_MS = 10000;

function readAutoRefresh() {
  try {
    return window.localStorage.getItem(AUTO_REFRESH_KEY) === "on";
  } catch {
    return false;
  }
}

function writeAutoRefresh(on) {
  try {
    window.localStorage.setItem(AUTO_REFRESH_KEY, on ? "on" : "off");
  } catch {
    // A preference that cannot be kept is not an error.
  }
}

// A toolbar button's label never breaks onto a second line.
const TOOLBAR_BUTTON_SX = { whiteSpace: "nowrap", flexShrink: 0 };

function SavedFilters({ query, onApply }) {
  const [anchor, setAnchor] = useState(null);
  const [views, setViews] = useState(loadSavedViews);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  // The saved filter a Delete asked to delete, until the user confirms.
  const [deleting, setDeleting] = useState(null);
  const current = cleanQuery(query);

  const close = () => setAnchor(null);
  const save = () => {
    setViews(saveView(name, current));
    setNaming(false);
    setName("");
    enqueueSnackbar(`Saved "${name.trim()}" in this browser`, {
      variant: "success",
    });
  };

  return (
    <>
      <Button
        variant="outlined"
        size="small"
        startIcon={<GatewayIcon name="bookmark" size={16} />}
        onClick={(event) => {
          setViews(loadSavedViews());
          setAnchor(event.currentTarget);
        }}
        aria-label="Saved filters"
        aria-haspopup="menu"
        aria-expanded={Boolean(anchor)}
        sx={TOOLBAR_BUTTON_SX}
      >
        {/* On a phone the three toolbar buttons share one row. */}
        <Box component="span" sx={{ display: { xs: "none", sm: "inline" } }}>
          Saved filters
        </Box>
        <Box component="span" sx={{ display: { xs: "inline", sm: "none" } }}>
          Saved
        </Box>
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        MenuListProps={{ dense: true, sx: { minWidth: 260 } }}
      >
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ px: 2, py: 0.75, display: "block" }}
        >
          Saved in this browser
        </Typography>
        {views.length === 0 && (
          <Typography
            typography="s2"
            color="text.disabled"
            sx={{ px: 2, pb: 1, display: "block" }}
          >
            No saved filters
          </Typography>
        )}
        {views.map((view) => (
          <MenuItem
            key={view.name}
            onClick={() => {
              onApply(view.query);
              close();
            }}
            selected={view.query === current && current !== ""}
          >
            <ListItemText primary={view.name} />
            <IconButton
              size="small"
              edge="end"
              aria-label={`Delete saved filter ${view.name}`}
              onClick={(event) => {
                event.stopPropagation();
                close();
                setDeleting(view.name);
              }}
            >
              <GatewayIcon name="trash" size={14} />
            </IconButton>
          </MenuItem>
        ))}
        <Divider />
        <MenuItem
          disabled={current === ""}
          onClick={() => {
            close();
            setNaming(true);
          }}
        >
          <ListItemText primary="Save current filters" />
        </MenuItem>
      </Menu>
      <Dialog
        open={naming}
        onClose={() => setNaming(false)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Save filters</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            size="small"
            label="Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && name.trim()) save();
            }}
            helperText="Kept in this browser only."
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setNaming(false)}>Cancel</Button>
          <Button
            variant="contained"
            color="primary"
            disabled={!name.trim()}
            onClick={save}
          >
            Save
          </Button>
        </DialogActions>
      </Dialog>
      <ConfirmDialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title={`Delete saved filter ${deleting ?? ""}?`}
        message="It is removed from this browser. The requests it matched are not changed."
        confirmLabel="Delete filter"
        confirmColor="error"
        onConfirm={() => {
          setViews(deleteSavedView(deleting));
          setDeleting(null);
        }}
      />
    </>
  );
}

SavedFilters.propTypes = {
  query: PropTypes.string.isRequired,
  onApply: PropTypes.func.isRequired,
};

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

// Counts what the table's query matches (the list's count, one row), states
// the scope and the cap, then downloads exactly the table's query: its
// filters, search and order. A refusal shows the server's message here.
function ExportDialog({ format, filters, onClose }) {
  const open = Boolean(format);
  // Keyed by value: the page passes a new filters object on every render,
  // and the count should run once per query, not once per render.
  const paramsKey = JSON.stringify(requestLogQueryParams(filters));
  const params = useMemo(() => JSON.parse(paramsKey), [paramsKey]);
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
      await downloadLogExport(filters, format);
      onClose();
    } catch (err) {
      setError(await exportErrorMessage(err));
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
          color="primary"
          onClick={handleDownload}
          disabled={count.state === "loading" || nothingToExport || downloading}
        >
          {downloading ? "Downloading..." : "Download"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

ExportDialog.propTypes = {
  format: PropTypes.oneOf(["csv", "json", null]),
  filters: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
};

// Export: the menu picks the format; the dialog says what the file will
// hold before anything downloads.
function ExportMenu({ filters }) {
  const [anchor, setAnchor] = useState(null);
  const [format, setFormat] = useState(null);
  const pick = (next) => {
    setAnchor(null);
    setFormat(next);
  };
  return (
    <>
      <Button
        variant="outlined"
        size="small"
        startIcon={<GatewayIcon name="download" size={16} />}
        onClick={(event) => setAnchor(event.currentTarget)}
        aria-haspopup="menu"
      >
        Export
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        MenuListProps={{ dense: true }}
      >
        <MenuItem onClick={() => pick("csv")}>Export CSV</MenuItem>
        <MenuItem onClick={() => pick("json")}>Export JSON</MenuItem>
      </Menu>
      <ExportDialog
        format={format}
        filters={filters}
        onClose={() => setFormat(null)}
      />
    </>
  );
}

ExportMenu.propTypes = { filters: PropTypes.object.isRequired };

const RequestExplorerSection = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const { filters, setFilter, setFilters, clearFilters, activeFilterCount } =
    useFilters();
  const queryClient = useQueryClient();

  // The open request is in the URL, not a filter of the list.
  const { log: openLogId, ...listFilters } = filters;

  const [filterPanelOpen, setFilterPanelOpen] = useState(false);
  // Guardrails and Cache hits are offered only where the gateway runs them.
  const gatewayConfig = useGatewayConfig(
    useContext(GatewayContext)?.gatewayId,
    { meta: { errorHandled: true } },
  );
  const hiddenQuick = hiddenQuickFilters(gatewayConfig);
  // Whether this deployment offers managed models, for the Filters panel's
  // Provider and Billing mode fields and the Columns picker's managed-only
  // columns (the AI Gateway rail has usually read it already), and false
  // until it says so.
  const managedStatus = useManagedModelsStatus();
  const managedOffered = managedStatus.data?.available === true;
  // Whether a row the table lists is a managed call (its billing mode, as the
  // Provider cell names it "Future AGI managed"): the panel then offers that
  // provider even where managed models are not offered.
  const [managedLogged, setManagedLogged] = useState(false);
  const handleResults = useCallback((results) => {
    setManagedLogged(
      Array.isArray(results) &&
        results.some((row) => isManagedMode(row?.billing_mode)),
    );
  }, []);
  const [columnsAnchor, setColumnsAnchor] = useState(null);
  const [searchValue, setSearchValue] = useState(filters.search || "");
  const [autoRefresh, setAutoRefresh] = useState(readAutoRefresh);

  // Credential type, Upstream cost and Markup are offered only while this
  // deployment makes managed models available. Historical rows do not enable them.
  const requestColumns = useRequestColumns({
    enabled: true,
    managed: managedOffered,
  });

  // Debounced search into the URL.
  useEffect(() => {
    const timer = setTimeout(() => {
      if (searchValue !== (filters.search || "")) {
        setFilters({
          ...filters,
          search: searchValue || undefined,
          page: undefined,
        });
      }
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchValue]);

  useEffect(() => {
    setSearchValue(filters.search || "");
  }, [filters.search]);

  const quick = activeQuickFilter(listFilters);
  const chips = useMemo(() => appliedFilterChips(listFilters), [listFilters]);

  const page = parseInt(listFilters.page, 10) || 1;
  const newestFirst = !listFilters.sort || listFilters.sort === "-started_at";
  const canAutoRefresh = page === 1 && newestFirst;

  const handleQuick = useCallback(
    (q) => {
      if (q.key === "all") {
        clearFilters();
        return;
      }
      setFilters(withQuickFilter(listFilters, q));
    },
    [clearFilters, listFilters, setFilters],
  );

  const handleApplyFilters = useCallback(
    (next) => {
      setFilters(next);
      setFilterPanelOpen(false);
    },
    [setFilters],
  );

  const openLog = useCallback(
    (id) => setFilter("log", id || undefined),
    [setFilter],
  );

  // The page's first Tab stop jumps over the toolbar to the first log row
  //.
  const gridApi = useRef(null);
  const onGridReady = useCallback((event) => {
    gridApi.current = event.api;
  }, []);
  const focusFirstRow = useCallback(() => {
    const api = gridApi.current;
    const [first] = api?.getAllDisplayedColumns?.() || [];
    if (!first || !api.getDisplayedRowCount()) return;
    api.ensureIndexVisible(0);
    api.setFocusedCell(0, first);
  }, []);

  if (filters.view === "sessions") {
    // The old Requests | Sessions tab: sessions have their own page.
    return <Navigate to={paths.dashboard.gateway.sessions} replace />;
  }

  return (
    <GatewayPageFrame
      title="Request logs"
      skipLink={{
        label: "Skip to the request list",
        onActivate: focusFirstRow,
      }}
      description={`Every request through the gateway, newest first. Times are in ${localZoneName()}.`}
      actions={
        <>
          <Tooltip
            title={
              canAutoRefresh
                ? "Reloads the newest requests every 10 seconds"
                : "Auto-refresh follows the newest requests: page 1, newest first"
            }
          >
            <FormControlLabel
              sx={{
                mr: 0.5,
                "& .MuiFormControlLabel-label": { typography: "s1" },
              }}
              control={
                <Switch
                  size="small"
                  checked={autoRefresh && canAutoRefresh}
                  disabled={!canAutoRefresh}
                  onChange={(event) => {
                    setAutoRefresh(event.target.checked);
                    writeAutoRefresh(event.target.checked);
                  }}
                  inputProps={{
                    role: "switch",
                    "aria-label": "Auto-refresh every 10 seconds",
                  }}
                />
              }
              label="Auto-refresh"
            />
          </Tooltip>
          <Tooltip title="Refresh">
            <IconButton
              size="small"
              aria-label="Refresh"
              onClick={() =>
                queryClient.invalidateQueries({ queryKey: ["requestLogs"] })
              }
            >
              <GatewayIcon name="refresh" size={16} />
            </IconButton>
          </Tooltip>
          <ExportMenu filters={listFilters} />
        </>
      }
    >
      <GatewayFilterBar
        search={searchValue}
        onSearchChange={setSearchValue}
        searchPlaceholder="Search model, provider, request id, error"
        actions={
          <>
            <SavedFilters
              query={searchParams.toString()}
              onApply={(query) => setSearchParams(new URLSearchParams(query))}
            />
            <Badge badgeContent={activeFilterCount} color="primary">
              <Button
                variant="outlined"
                size="small"
                startIcon={<GatewayIcon name="filter" size={16} />}
                onClick={() => setFilterPanelOpen(true)}
                sx={TOOLBAR_BUTTON_SX}
              >
                Filters
              </Button>
            </Badge>
            <Button
              variant="outlined"
              size="small"
              startIcon={<GatewayIcon name="columns" size={16} />}
              onClick={(event) => setColumnsAnchor(event.currentTarget)}
              // Edits need the user/org storage key to be saved.
              disabled={!requestColumns.storageKey}
              aria-haspopup="dialog"
              aria-expanded={Boolean(columnsAnchor)}
              aria-controls={columnsAnchor ? COLUMNS_POPOVER_ID : undefined}
              sx={TOOLBAR_BUTTON_SX}
            >
              Columns
            </Button>
          </>
        }
      >
        <Box
          sx={{ display: "flex", gap: 0.75, flexWrap: "wrap" }}
          role="group"
          aria-label="Quick filters"
        >
          {QUICK_FILTERS.filter((q) => !hiddenQuick.has(q.key)).map((q) => (
            <Chip
              key={q.key}
              label={q.label}
              size="small"
              variant={quick === q.key ? "filled" : "outlined"}
              color={quick === q.key ? "primary" : "default"}
              onClick={() => handleQuick(q)}
              component="button"
              aria-pressed={quick === q.key}
            />
          ))}
        </Box>
      </GatewayFilterBar>

      {chips.length > 0 && (
        <Box
          component="ul"
          aria-label="Applied filters"
          sx={{
            display: "flex",
            flexWrap: "wrap",
            gap: 0.75,
            m: 0,
            p: 0,
            listStyle: "none",
            alignItems: "center",
          }}
        >
          {chips.map((chip) => (
            <Box component="li" key={chip.key}>
              <Chip
                size="small"
                variant="outlined"
                label={chip.label}
                onDelete={() =>
                  setFilters({
                    ...listFilters,
                    [chip.key]: undefined,
                    page: undefined,
                  })
                }
                deleteIcon={
                  <Box
                    component="span"
                    role="button"
                    aria-label={`Remove ${chip.label}`}
                    sx={{ display: "inline-flex" }}
                  >
                    <GatewayIcon name="close" size={12} />
                  </Box>
                }
              />
            </Box>
          ))}
          <Box component="li">
            <Button size="small" onClick={clearFilters}>
              Clear all
            </Button>
          </Box>
        </Box>
      )}

      <RequestVolumeStrip filters={listFilters} />

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

      <RequestTable
        filters={listFilters}
        setFilter={setFilter}
        setFilters={setFilters}
        onSelectLog={openLog}
        onClearFilters={clearFilters}
        columns={requestColumns.columns}
        onGridReady={onGridReady}
        onResults={handleResults}
        refetchInterval={
          autoRefresh && canAutoRefresh ? AUTO_REFRESH_MS : false
        }
      />

      <RequestDetailDrawer
        logId={openLogId || null}
        open={Boolean(openLogId)}
        onClose={() => openLog(null)}
      />

      <FilterPanel
        open={filterPanelOpen}
        onClose={() => setFilterPanelOpen(false)}
        filters={listFilters}
        onApply={handleApplyFilters}
        managedOffered={managedOffered}
        managedLogged={managedLogged}
        hideCacheHits={hiddenQuick.has("cache")}
        hideGuardrails={hiddenQuick.has("guardrails")}
      />
    </GatewayPageFrame>
  );
};

export default RequestExplorerSection;

import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Card,
  Chip,
  IconButton,
  InputAdornment,
  MenuItem,
  Skeleton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { fToNow } from "src/utils/format-time";
import GatewayEmptyState from "../components/GatewayEmptyState";
import PageErrorState from "../components/PageErrorState";
import { formatDateTime } from "../utils/formatters";
import ModelDetailDrawer from "./ModelDetailDrawer";
import { copyText } from "./copyText";
import {
  CAPABILITIES,
  CONTEXT_OPTIONS,
  DEFAULT_FILTERS,
  SORT_OPTIONS,
  filterModels,
  formatPercent,
  formatPerMillion,
  formatTokenCount,
  hasListPrice,
  modelDisplayName,
  sortModels,
  vendorLabel,
  vendorOptions,
} from "./catalog";

const PAGE_SIZE_OPTIONS = [25, 50, 100];

// Columns that give way on narrow screens, so the name and prices stay legible.
const HIDE_BELOW_MD = { display: { xs: "none", md: "table-cell" } };
const HIDE_BELOW_SM = { display: { xs: "none", sm: "table-cell" } };

function PriceCell({ model, field, upstreamField, markupPercent }) {
  if (!hasListPrice(model)) {
    return (
      <Tooltip
        title={`Priced per request by the provider. Billed at provider cost + ${formatPercent(markupPercent)}.`}
        arrow
      >
        <Typography variant="body2" color="text.secondary" component="span">
          Varies
        </Typography>
      </Tooltip>
    );
  }
  const value = model[field];
  return (
    <Tooltip
      title={`Provider price ${formatPerMillion(model[upstreamField])} + ${formatPercent(markupPercent)}`}
      arrow
    >
      <Typography
        variant="body2"
        component="span"
        sx={{ fontVariantNumeric: "tabular-nums" }}
      >
        {formatPerMillion(value)}
      </Typography>
    </Tooltip>
  );
}

PriceCell.propTypes = {
  model: PropTypes.object.isRequired,
  field: PropTypes.string.isRequired,
  upstreamField: PropTypes.string.isRequired,
  markupPercent: PropTypes.number,
};

// Every capability keeps its slot so the icons line up down the column.
function CapabilityIcons({ model }) {
  return (
    <Stack direction="row" spacing={0.75}>
      {CAPABILITIES.map((c) => {
        const supported = c.test(model);
        return supported ? (
          <Tooltip key={c.key} title={c.label} arrow>
            <Iconify
              icon={c.icon}
              width={18}
              aria-label={c.label}
              sx={{ color: "text.secondary" }}
            />
          </Tooltip>
        ) : (
          <Box key={c.key} sx={{ width: 18, height: 18 }} />
        );
      })}
    </Stack>
  );
}

CapabilityIcons.propTypes = { model: PropTypes.object.isRequired };

function CatalogFreshness({ providers }) {
  const list = Array.isArray(providers) ? providers : [];
  if (!list.length) return null;
  const bundled = list.some((p) => p.source !== "live");
  const latest = list
    .map((p) => p.fetched_at)
    .filter(Boolean)
    .sort()
    .pop();
  const when = latest ? fToNow(latest) : "";
  const text = bundled
    ? "Using the bundled price list; live prices are unavailable"
    : `Prices refreshed ${when || "recently"}`;
  return (
    <Tooltip
      title={latest ? `Catalog fetched ${formatDateTime(latest)}` : ""}
      arrow
    >
      <Stack
        direction="row"
        spacing={0.5}
        alignItems="center"
        sx={{ color: bundled ? "warning.main" : "text.secondary" }}
      >
        <Iconify
          icon={bundled ? "mdi:clock-alert-outline" : "mdi:update"}
          width={14}
        />
        <Typography variant="caption">{text}</Typography>
      </Stack>
    </Tooltip>
  );
}

CatalogFreshness.propTypes = { providers: PropTypes.array };

function CatalogSkeleton() {
  return (
    <Card>
      {[...Array(8)].map((_, i) => (
        <Stack
          key={i}
          direction="row"
          spacing={2}
          sx={{
            px: 2,
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Skeleton width="35%" height={20} />
          <Skeleton width="15%" height={20} />
          <Skeleton width="10%" height={20} />
          <Skeleton width="12%" height={20} />
          <Skeleton width="12%" height={20} />
        </Stack>
      ))}
    </Card>
  );
}

const ModelCatalog = ({
  catalog,
  isLoading,
  error,
  onRetry,
  managedStatus,
  baseUrl,
}) => {
  const navigate = useNavigate();
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const [selectedId, setSelectedId] = useState(null);

  const models = useMemo(
    () => (Array.isArray(catalog?.models) ? catalog.models : []),
    [catalog],
  );
  const markupPercent =
    catalog?.markup_percent ?? managedStatus?.markup_percent;
  const vendors = useMemo(() => vendorOptions(models), [models]);
  const visible = useMemo(
    () => sortModels(filterModels(models, filters), filters.sort),
    [models, filters],
  );
  const pageRows = visible.slice(page * pageSize, (page + 1) * pageSize);
  const selected = models.find((m) => m.id === selectedId) || null;
  const isFiltered =
    filters.search ||
    filters.vendor ||
    filters.capabilities.length ||
    filters.minContext;

  const setFilter = (key, value) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(0);
  };

  const toggleCapability = (key) =>
    setFilter(
      "capabilities",
      filters.capabilities.includes(key)
        ? filters.capabilities.filter((k) => k !== key)
        : [...filters.capabilities, key],
    );

  const clearFilters = () => {
    setFilters((prev) => ({ ...DEFAULT_FILTERS, sort: prev.sort }));
    setPage(0);
  };

  if (managedStatus && !managedStatus.available) {
    return (
      <Card variant="outlined">
        <GatewayEmptyState
          title="No managed catalog on this deployment"
          description="This gateway serves models through your own provider keys. Add a provider to choose which of its models the gateway can call."
          actionLabel="Go to Providers"
          onAction={() => navigate(paths.dashboard.gateway.providers)}
        />
      </Card>
    );
  }

  if (isLoading) return <CatalogSkeleton />;

  if (error) {
    return (
      <PageErrorState
        message={`Couldn't load the model catalog${error.message ? `: ${error.message}` : "."}`}
        onRetry={onRetry}
      />
    );
  }

  return (
    <>
      {/* ---- Filters ---- */}
      <Stack
        direction={{ xs: "column", md: "row" }}
        spacing={1.5}
        mb={1.5}
        alignItems={{ xs: "stretch", md: "center" }}
      >
        <TextField
          placeholder="Search by model, ID or vendor"
          size="small"
          value={filters.search}
          onChange={(e) => setFilter("search", e.target.value)}
          sx={{ flex: 1, maxWidth: { md: 380 } }}
          inputProps={{ "aria-label": "Search models" }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="eva:search-outline" width={18} />
              </InputAdornment>
            ),
          }}
        />
        <Box
          sx={{
            display: "grid",
            gap: 1.5,
            gridTemplateColumns: {
              xs: "repeat(2, minmax(0, 1fr))",
              sm: "repeat(3, minmax(0, 1fr))",
              md: "repeat(3, auto)",
            },
            "& > :last-child": { gridColumn: { xs: "1 / -1", sm: "auto" } },
          }}
        >
          <TextField
            select
            size="small"
            label="Vendor"
            value={filters.vendor}
            onChange={(e) => setFilter("vendor", e.target.value)}
            sx={{ minWidth: { md: 150 } }}
            // "" is "All vendors"; without these the field renders blank.
            InputLabelProps={{ shrink: true }}
            SelectProps={{
              displayEmpty: true,
              MenuProps: { PaperProps: { sx: { maxHeight: 360 } } },
            }}
          >
            <MenuItem value="">All vendors</MenuItem>
            {vendors.map((v) => (
              <MenuItem key={v.value} value={v.value}>
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  sx={{ width: "100%", gap: 2 }}
                >
                  <span>{v.label}</span>
                  <Typography
                    component="span"
                    variant="caption"
                    color="text.disabled"
                  >
                    {v.count}
                  </Typography>
                </Stack>
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            label="Context"
            value={filters.minContext}
            onChange={(e) => setFilter("minContext", Number(e.target.value))}
            sx={{ minWidth: { md: 140 } }}
          >
            {CONTEXT_OPTIONS.map((o) => (
              <MenuItem key={o.value} value={o.value}>
                {o.label}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            label="Sort"
            value={filters.sort}
            onChange={(e) => setFilter("sort", e.target.value)}
            sx={{ minWidth: { md: 170 } }}
          >
            {SORT_OPTIONS.map((o) => (
              <MenuItem key={o.value} value={o.value}>
                {o.label}
              </MenuItem>
            ))}
          </TextField>
        </Box>
      </Stack>

      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ xs: "flex-start", sm: "center" }}
        spacing={1}
        mb={2}
      >
        <Stack
          direction="row"
          spacing={1}
          flexWrap="wrap"
          useFlexGap
          role="group"
          aria-label="Filter by capability"
        >
          {CAPABILITIES.map((c) => {
            const active = filters.capabilities.includes(c.key);
            return (
              <Chip
                key={c.key}
                size="small"
                label={c.label}
                icon={<Iconify icon={c.icon} width={16} />}
                color={active ? "primary" : "default"}
                variant={active ? "filled" : "outlined"}
                onClick={() => toggleCapability(c.key)}
                aria-pressed={active}
              />
            );
          })}
          {isFiltered ? (
            <Chip
              size="small"
              variant="outlined"
              label="Clear filters"
              onClick={clearFilters}
              icon={<Iconify icon="mdi:filter-remove-outline" width={16} />}
            />
          ) : null}
        </Stack>
        <Stack
          direction="row"
          spacing={2}
          alignItems="center"
          sx={{ flexShrink: 0 }}
        >
          <Typography variant="caption" color="text.secondary">
            {visible.length === models.length
              ? `${models.length} models`
              : `${visible.length} of ${models.length} models`}
          </Typography>
          <CatalogFreshness providers={catalog?.providers} />
        </Stack>
      </Stack>

      {/* ---- Table ---- */}
      {visible.length === 0 ? (
        <Card variant="outlined">
          <GatewayEmptyState
            variant={models.length ? "no-results" : "no-data"}
            title={
              models.length
                ? "No models match these filters"
                : "The catalog is empty"
            }
            description={
              models.length
                ? "Try a broader search, another vendor, or fewer capabilities."
                : "The gateway has not loaded any managed models yet. Try again in a few minutes."
            }
            onAction={models.length ? clearFilters : onRetry}
            actionLabel={models.length ? "Clear Filters" : "Retry"}
          />
        </Card>
      ) : (
        <Card>
          <TableContainer
            sx={{ maxHeight: "calc(100vh - 380px)", minHeight: 320 }}
          >
            <Table stickyHeader size="small" aria-label="Model catalog">
              <TableHead>
                <TableRow>
                  <TableCell sx={{ fontWeight: 600 }}>Model</TableCell>
                  <TableCell sx={{ fontWeight: 600, ...HIDE_BELOW_MD }}>
                    Vendor
                  </TableCell>
                  <TableCell
                    sx={{ fontWeight: 600, ...HIDE_BELOW_SM }}
                    align="right"
                  >
                    Context
                  </TableCell>
                  <TableCell
                    sx={{ fontWeight: 600, whiteSpace: "nowrap" }}
                    align="right"
                  >
                    Input / 1M
                  </TableCell>
                  <TableCell
                    sx={{ fontWeight: 600, whiteSpace: "nowrap" }}
                    align="right"
                  >
                    Output / 1M
                  </TableCell>
                  <TableCell sx={{ fontWeight: 600, ...HIDE_BELOW_MD }}>
                    Capabilities
                  </TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {pageRows.map((m) => (
                  <TableRow
                    key={m.id}
                    hover
                    tabIndex={0}
                    sx={{ cursor: "pointer" }}
                    onClick={() => setSelectedId(m.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") setSelectedId(m.id);
                    }}
                    aria-label={`${modelDisplayName(m)} details`}
                  >
                    <TableCell sx={{ maxWidth: 0, width: { md: "38%" } }}>
                      <Typography variant="body2" fontWeight={500} noWrap>
                        {modelDisplayName(m)}
                      </Typography>
                      <Stack direction="row" alignItems="center" spacing={0.25}>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          noWrap
                          sx={{ fontFamily: "monospace" }}
                        >
                          {m.id}
                        </Typography>
                        <Tooltip title="Copy model ID" arrow>
                          <IconButton
                            size="small"
                            aria-label={`Copy ${m.id}`}
                            sx={{ p: 0.25 }}
                            onClick={(e) => {
                              e.stopPropagation();
                              copyText(m.id, "Model ID copied");
                            }}
                          >
                            <Iconify icon="mdi:content-copy" width={13} />
                          </IconButton>
                        </Tooltip>
                      </Stack>
                    </TableCell>
                    <TableCell sx={HIDE_BELOW_MD}>
                      <Typography variant="body2" color="text.secondary" noWrap>
                        {vendorLabel(m.vendor)}
                      </Typography>
                    </TableCell>
                    <TableCell sx={HIDE_BELOW_SM} align="right">
                      <Typography
                        variant="body2"
                        sx={{ fontVariantNumeric: "tabular-nums" }}
                      >
                        {formatTokenCount(m.context_length)}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      <PriceCell
                        model={m}
                        field="billed_input_per_mtok"
                        upstreamField="input_per_mtok"
                        markupPercent={markupPercent}
                      />
                    </TableCell>
                    <TableCell align="right">
                      <PriceCell
                        model={m}
                        field="billed_output_per_mtok"
                        upstreamField="output_per_mtok"
                        markupPercent={markupPercent}
                      />
                    </TableCell>
                    <TableCell sx={HIDE_BELOW_MD}>
                      <CapabilityIcons model={m} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          <TablePagination
            component="div"
            count={visible.length}
            page={page}
            rowsPerPage={pageSize}
            rowsPerPageOptions={PAGE_SIZE_OPTIONS}
            onPageChange={(_, p) => setPage(p)}
            onRowsPerPageChange={(e) => {
              setPageSize(Number(e.target.value));
              setPage(0);
            }}
          />
        </Card>
      )}

      <Typography
        variant="caption"
        color="text.secondary"
        component="p"
        sx={{ mt: 1.5 }}
      >
        Prices are what managed requests are billed: provider price +{" "}
        {formatPercent(markupPercent)}. Requests through your own provider keys
        carry no markup.
      </Typography>

      <ModelDetailDrawer
        model={selected}
        open={Boolean(selected)}
        onClose={() => setSelectedId(null)}
        markupPercent={markupPercent}
        baseUrl={baseUrl}
        managedStatus={managedStatus}
      />
    </>
  );
};

ModelCatalog.propTypes = {
  catalog: PropTypes.shape({
    markup_percent: PropTypes.number,
    providers: PropTypes.array,
    models: PropTypes.array,
  }),
  isLoading: PropTypes.bool,
  error: PropTypes.object,
  onRetry: PropTypes.func,
  managedStatus: PropTypes.object,
  baseUrl: PropTypes.string,
};

export default ModelCatalog;

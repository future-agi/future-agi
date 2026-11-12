import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Badge,
  Box,
  Button,
  Chip,
  Drawer,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";

import SvgColor from "src/components/svg-color";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { useResponsive } from "src/hooks/use-responsive";
import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayPageFrame,
} from "../../components/frame";
import { migrateColumnState } from "../../components/frame/column-state";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import ManagedModelsCard from "../ManagedModelsCard";
import { catalogFreshness, vendorLabel } from "../catalog";
import { catalogFacets, filterRows } from "../catalogRows";
import { useCatalogRows } from "../hooks/useCatalogRows";
import {
  CATALOG_LAYOUT_KEY,
  CATALOG_LAYOUT_V2_KEY,
  CENTRED,
  FACET_PANEL_WIDTH,
  catalogColumns,
  fromV2Layout,
} from "./columns";
import CatalogFacets from "./CatalogFacets";
import { SORT_PRESETS, useCatalogBrowse } from "./queryState";
import { STICKY_TOP_PX, useStickyFit } from "./stickyFit";
import {
  CompareToolbar,
  ModelCompareProvider,
} from "../compare/CompareControls";
import { withCompareSelection } from "../compare/compareColumns";

const ROW_HEIGHT = 52;
const CATALOG_COL_DEF = { cellStyle: CENTRED };
const activeFacets = (f) =>
  f.type.length +
  f.vendor.length +
  f.access.length +
  (f.price ? 1 : 0) +
  (f.minContext ? 1 : 0) +
  f.capabilities.length +
  f.parameters.length +
  f.inputModalities.length;
const isFiltered = (f) => Boolean(f.search) || activeFacets(f) > 0;

function Freshness({ catalog }) {
  const freshness = catalogFreshness(catalog?.providers);
  if (!freshness) return null;
  return (
    <Tooltip
      title={
        <Box component="span" sx={{ whiteSpace: "pre-line" }}>
          {freshness.tip}
        </Box>
      }
      arrow
      describeChild
    >
      <Box
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          minWidth: 0,
          color: "text.secondary",
        }}
      >
        <SvgColor
          src="/assets/icons/ic_info.svg"
          sx={{ width: 14, height: 14, flexShrink: 0 }}
        />
        <Typography typography="s2" color="text.secondary" noWrap>
          {freshness.text}
        </Typography>
      </Box>
    </Tooltip>
  );
}
Freshness.propTypes = { catalog: PropTypes.object };

export default function ModelCatalogPage() {
  useState(() =>
    migrateColumnState(CATALOG_LAYOUT_V2_KEY, CATALOG_LAYOUT_KEY, fromV2Layout),
  );
  const { canEdit: canWrite } = useGatewayAdmin();
  const data = useCatalogRows();
  const {
    status,
    catalog,
    providers,
    providerAccessState,
    rows,
    markupPercent,
  } = data;
  const {
    filters,
    sort,
    setFilter,
    clearFilters,
    setSort,
    openModel,
    gridEvents,
  } = useCatalogBrowse(CATALOG_LAYOUT_KEY, ROW_HEIGHT);
  const compact = useResponsive("down", "md");
  // Below lg the columns run past the grid's edge beside the facet panel;
  // the model name stays pinned in view (not on a phone, where a pinned
  // column eats the little room there is).
  const belowLg = useResponsive("down", "lg");
  const pinName = belowLg && !compact;
  const [facetsOpen, setFacetsOpen] = useState(false);
  const [panelRef, panelMaxHeight] = useStickyFit();

  const all = useMemo(() => rows || [], [rows]);
  const visible = useMemo(
    () => (rows ? filterRows(rows, filters) : undefined),
    [rows, filters],
  );
  const inclusivePrices = all.some((row) => row.inclusivePricing);
  const columns = useMemo(
    () =>
      catalogColumns({
        markupPercent,
        inclusivePrices,
        onOpenModel: openModel,
        pinName,
      })
        .map(withCompareSelection)
        .map(
          ({ sort: initialSort, sortIndex: initialSortIndex, ...column }) => ({
            ...column,
            initialSort,
            initialSortIndex,
          }),
        ),
    [markupPercent, inclusivePrices, openModel, pinName],
  );
  const facets = useMemo(() => catalogFacets(all, filters), [all, filters]);
  const selectedFilters = facets.flatMap((group) => {
    const selected = group.multiple
      ? filters[group.key]
      : filters[group.key]
        ? [filters[group.key]]
        : [];
    return selected.map((value) => ({
      key: `${group.key}:${value}`,
      label: `${group.label}: ${group.options.find((option) => option.value === value)?.label || (group.key === "vendor" ? vendorLabel(value) : value)}`,
      remove: () =>
        setFilter(
          group.key,
          group.multiple
            ? filters[group.key].filter((item) => item !== value)
            : group.key === "minContext"
              ? 0
              : "",
        ),
    }));
  });
  if (filters.search)
    selectedFilters.unshift({
      key: "search",
      label: `Search: ${filters.search}`,
      remove: () => setFilter("search", ""),
    });

  if (status.data && !data.offered) return null;
  const statusFailed = Boolean(status.error) && !status.data;
  let body;
  if (statusFailed) {
    body = (
      <GatewayEmptyState
        icon="catalog"
        title="Couldn't load the model catalog"
        description="The gateway did not say whether Future AGI models are available. Try again in a moment."
        actionLabel="Retry"
        onAction={() => status.refetch()}
      />
    );
  } else if (catalog.error && !rows) {
    body = (
      <GatewayEmptyState
        icon="catalog"
        title="Couldn't load the model catalog"
        description={
          catalog.error.message ||
          "The gateway did not answer. Try again in a moment."
        }
        actionLabel="Retry"
        onAction={() => catalog.refetch()}
      />
    );
  } else if (rows && rows.length === 0 && !catalog.error && !status.error) {
    body = (
      <GatewayEmptyState
        icon="catalog"
        title="The catalog is empty"
        description="The gateway has not loaded any Future AGI models yet. Try again in a few minutes."
        actionLabel="Retry"
        onAction={() => catalog.refetch()}
      />
    );
  } else {
    const panel = (
      <CatalogFacets
        facets={facets}
        filters={filters}
        onChange={setFilter}
        onClear={isFiltered(filters) ? clearFilters : undefined}
        onClose={compact ? () => setFacetsOpen(false) : undefined}
      />
    );
    // The panel's own scroll: it stops at its ends instead of moving the
    // page, keeps room for its scrollbar so the options never shift, and
    // draws a thin bar rather than the platform's wide one.
    const panelScrollSx = {
      overflowY: "auto",
      overscrollBehavior: "contain",
      // Keyboard focus moved above the visible part stops below the pinned
      // title instead of under it.
      scrollPaddingTop: "48px",
      scrollbarGutter: "stable",
      scrollbarWidth: "thin",
      "&::-webkit-scrollbar": { width: 6 },
      "&::-webkit-scrollbar-thumb": {
        borderRadius: 3,
        bgcolor: "divider",
      },
    };
    body = (
      <Box
        sx={{
          flex: "1 1 0",
          minHeight: 0,
          display: "flex",
          flexDirection: { xs: "column", md: "row" },
          gap: 2,
        }}
      >
        {compact ? (
          <Box>
            <Badge
              badgeContent={activeFacets(filters)}
              color="primary"
              invisible={!activeFacets(filters)}
            >
              <Button
                variant="outlined"
                size="small"
                aria-expanded={facetsOpen}
                onClick={() => setFacetsOpen((open) => !open)}
              >
                Filters
              </Button>
            </Badge>
            <Drawer
              anchor="left"
              open={facetsOpen}
              onClose={() => setFacetsOpen(false)}
              PaperProps={{
                role: "dialog",
                "aria-modal": true,
                "aria-label": "Filters",
                sx: {
                  width: "min(360px, calc(100vw - 48px))",
                  display: "flex",
                  flexDirection: "column",
                },
              }}
            >
              <Box sx={{ flex: 1, minHeight: 0, px: 2, ...panelScrollSx }}>
                {panel}
              </Box>
              <Box
                sx={{
                  flexShrink: 0,
                  p: 1.5,
                  borderTop: 1,
                  borderColor: "divider",
                }}
              >
                <Button
                  fullWidth
                  variant="contained"
                  onClick={() => setFacetsOpen(false)}
                >
                  {visible
                    ? `Show ${visible.length} ${visible.length === 1 ? "model" : "models"}`
                    : "Show models"}
                </Button>
              </Box>
            </Drawer>
          </Box>
        ) : (
          <Box
            ref={panelRef}
            data-testid="catalog-filter-panel"
            sx={{
              width: FACET_PANEL_WIDTH,
              flexShrink: 0,
              // Sticky beside the grid: stays in view while the page
              // scrolls, and scrolls its own groups within the window.
              alignSelf: "flex-start",
              position: "sticky",
              top: STICKY_TOP_PX,
              maxHeight: panelMaxHeight,
              pr: 0.5,
              ...panelScrollSx,
            }}
          >
            {panel}
          </Box>
        )}
        <Box
          sx={{
            flex: "1 1 0",
            minWidth: 0,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            gap: 1.5,
            "& > *": { flexShrink: 0 },
          }}
        >
          <GatewayFilterBar
            search={filters.search}
            onSearchChange={(value) => setFilter("search", value)}
            searchPlaceholder="Search models"
            actions={
              <Box
                sx={{
                  display: "flex",
                  flexWrap: "wrap",
                  alignItems: "center",
                  gap: 1.5,
                }}
              >
                <Typography typography="s2" color="text.secondary" noWrap>
                  {!visible
                    ? ""
                    : visible.length === all.length
                      ? `${all.length} models`
                      : `${visible.length} of ${all.length} models`}
                </Typography>
                <Freshness catalog={catalog.data} />
              </Box>
            }
          />
          <Box
            sx={{
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              gap: 1,
            }}
          >
            <TextField
              select
              label="Sort models"
              size="small"
              SelectProps={{ native: true }}
              value={
                SORT_PRESETS.some((preset) => preset.value === sort)
                  ? sort
                  : "custom"
              }
              onChange={(event) => setSort(event.target.value)}
              sx={{ width: { xs: "100%", sm: 224 } }}
            >
              {SORT_PRESETS.map((preset) => (
                <option key={preset.value} value={preset.value}>
                  {preset.label}
                </option>
              ))}
              {!SORT_PRESETS.some((preset) => preset.value === sort) && (
                <option value="custom" disabled>
                  {sort === "none" ? "No sorting" : "Custom column sort"}
                </option>
              )}
            </TextField>
            {isFiltered(filters) && (
              <Button size="small" onClick={clearFilters}>
                Clear filters
              </Button>
            )}
          </Box>
          {selectedFilters.length > 0 && (
            <Box
              role="group"
              aria-label="Active filters"
              sx={{ display: "flex", flexWrap: "wrap", gap: 0.75 }}
            >
              {selectedFilters.map(({ key, label, remove }) => (
                <Chip
                  key={key}
                  size="small"
                  label={label}
                  aria-label={`Remove ${label}`}
                  onClick={remove}
                  onDelete={remove}
                  sx={{ maxWidth: "100%" }}
                />
              ))}
            </Box>
          )}
          <CompareToolbar rows={all} />
          <GatewayGrid
            rowData={visible}
            loading={data.isLoading || !rows}
            columnDefs={columns}
            stateKey={CATALOG_LAYOUT_KEY}
            getRowId={(p) => p.data.id}
            rowHeight={ROW_HEIGHT}
            defaultColDef={CATALOG_COL_DEF}
            // Below 1280 px the columns run past the grid's right edge.
            scrollCue
            onRowClicked={(event) => {
              // AG Grid also receives native clicks on a disabled checkbox's
              // wrapper/icon. Those remain selection controls, not row links.
              if (!event.event?.target?.closest?.("[data-model-compare]"))
                openModel(event);
            }}
            {...gridEvents}
            // A layout saved at another width never decides the pin.
            onGridReady={(event) => {
              gridEvents.onGridReady(event);
              event.api.applyColumnState({
                state: [{ colId: "name", pinned: pinName ? "left" : null }],
              });
            }}
            rowStyle={{ cursor: "pointer" }}
            tooltipShowDelay={400}
            emptyState={{
              variant: "no-results",
              title:
                !all.length && (catalog.error || status.error)
                  ? "No saved catalog models to show"
                  : "No models match",
              description:
                !all.length && (catalog.error || status.error)
                  ? "Retry the failed read to check the current catalog."
                  : "Try a broader search, another type or provider, or fewer filters.",
              actionLabel: isFiltered(filters) ? "Clear filters" : undefined,
              onAction: clearFilters,
            }}
          />
          <Typography typography="s2" color="text.secondary" component="p">
            {inclusivePrices
              ? "Prices are what requests on Future AGI credits are billed. Token prices are per 1M tokens; inclusive quotes use each stated billing unit. Inclusive requests are billed from the usage the provider reports, not a sum of quoted dimensions. "
              : "Prices are per 1M tokens: what requests on Future AGI credits are billed. "}
            Requests through your own providers are billed by your provider.
          </Typography>
        </Box>
      </Box>
    );
  }
  return (
    <ModelCompareProvider data={data}>
      <GatewayPageFrame
        title="Models"
        description="Every model in the gateway's catalog, what it costs, and how your organization can call it."
        actions={
          <Button
            component={RouterLink}
            href={`${paths.dashboard.gateway.models}/your`}
            variant="outlined"
            size="small"
          >
            Your models
          </Button>
        }
      >
        {!status.error && (
          <ManagedModelsCard
            status={status.data}
            isLoading={status.isLoading}
            error={status.error}
            onRetry={status.refetch}
            gatewayId={data.gatewayId}
            canWrite={canWrite}
          />
        )}
        {rows &&
          (status.error ||
            catalog.error ||
            providerAccessState !== "ready") && (
            <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
              {status.error && (
                <Alert
                  severity="warning"
                  action={
                    <Button
                      color="inherit"
                      size="small"
                      disabled={status.isFetching}
                      onClick={() => status.refetch()}
                    >
                      Retry model status
                    </Button>
                  }
                >
                  Model status may be stale. Showing previously loaded catalog
                  facts.
                </Alert>
              )}
              {catalog.error && (
                <Alert
                  severity="warning"
                  action={
                    <Button
                      color="inherit"
                      size="small"
                      disabled={catalog.isFetching}
                      onClick={() => catalog.refetch()}
                    >
                      Retry catalog
                    </Button>
                  }
                >
                  Catalog facts may be stale. The refresh failed; your current
                  browse is preserved.
                </Alert>
              )}
              {providerAccessState !== "ready" && (
                <Alert
                  severity={
                    providerAccessState === "error" ? "warning" : "info"
                  }
                  action={
                    providerAccessState === "error" ? (
                      <Button
                        color="inherit"
                        size="small"
                        disabled={providers.isFetching}
                        onClick={() => providers.refetch()}
                      >
                        Retry provider access
                      </Button>
                    ) : undefined
                  }
                >
                  {providerAccessState === "pending"
                    ? "Checking provider access. Catalog facts remain available; own-key access is not verified yet."
                    : providers.data
                      ? "Provider access may be stale. Access not verified; retry before using your own key."
                      : "Provider access could not be verified. Catalog facts remain available; retry to check your own-key routes."}
                </Alert>
              )}
            </Box>
          )}
        {body}
      </GatewayPageFrame>
    </ModelCompareProvider>
  );
}

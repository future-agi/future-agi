import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Badge,
  Box,
  Button,
  Collapse,
  Tooltip,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";

import SvgColor from "src/components/svg-color";
import { useResponsive } from "src/hooks/use-responsive";

import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayPageFrame,
} from "../../components/frame";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import ManagedModelsCard from "../ManagedModelsCard";
import { catalogFreshness, formatPercent } from "../catalog";
import { catalogFacets, filterRows } from "../catalogRows";
import { useCatalogRows } from "../hooks/useCatalogRows";
import { modelPath } from "../modelLinks";
import { migrateColumnState } from "../../components/frame/column-state";
import {
  CATALOG_LAYOUT_KEY,
  CATALOG_LAYOUT_V2_KEY,
  CENTRED,
  FACET_PANEL_WIDTH,
  catalogColumns,
  fromV2Layout,
} from "./columns";
import CatalogFacets from "./CatalogFacets";

const CATALOG_COL_DEF = { cellStyle: CENTRED };

const DEFAULT_FILTERS = {
  search: "",
  type: [],
  vendor: [],
  access: [],
  price: "",
  minContext: 0,
  capabilities: [],
};

// How many facet choices are set (the search box aside).
const activeFacets = (f) =>
  f.type.length +
  f.vendor.length +
  f.access.length +
  (f.price ? 1 : 0) +
  (f.minContext ? 1 : 0) +
  f.capabilities.length;

const isFiltered = (f) => Boolean(f.search) || activeFacets(f) > 0;

// Where the prices come from, in one quiet line with the detail in its
// tooltip: a built-in price list is a fact about the catalog, not an error.
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
  const navigate = useNavigate();
  // A layout this browser saved before the facet panel is carried over once,
  // before the grid reads its layout (GatewayGrid onGridReady).
  useState(() =>
    migrateColumnState(CATALOG_LAYOUT_V2_KEY, CATALOG_LAYOUT_KEY, fromV2Layout),
  );
  // Turning managed models on or off writes the org's gateway config: Owner
  // or Admin only (useGatewayAdmin, the backend's is_gateway_admin).
  const { canEdit: canWrite } = useGatewayAdmin();
  const data = useCatalogRows();
  const { status, catalog, rows, markupPercent } = data;
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  // Below md the facets fold under a Filters button, so the grid stays on
  // the first screen at 390 px.
  const compact = useResponsive("down", "md");
  const [facetsOpen, setFacetsOpen] = useState(false);

  const setFilter = (key, value) =>
    setFilters((prev) => ({ ...prev, [key]: value }));
  const clearFilters = useCallback(() => setFilters(DEFAULT_FILTERS), []);

  const all = useMemo(() => rows || [], [rows]);
  const visible = useMemo(
    () => (rows ? filterRows(rows, filters) : undefined),
    [rows, filters],
  );
  const inclusivePrices = all.some((row) => row.inclusivePricing);
  const columns = useMemo(
    () => catalogColumns({ markupPercent, inclusivePrices }),
    [markupPercent, inclusivePrices],
  );

  const facets = useMemo(() => catalogFacets(all, filters), [all, filters]);

  const openModel = useCallback(
    (event) => {
      // The model name is a link that opens the page itself.
      if (event?.event?.target?.closest?.("a")) return;
      const path = modelPath(event?.data?.id);
      if (path) navigate(path);
    },
    [navigate],
  );

  const strip = (
    <ManagedModelsCard
      status={status.data}
      isLoading={status.isLoading}
      error={status.error}
      onRetry={status.refetch}
      gatewayId={data.gatewayId}
      canWrite={canWrite}
    />
  );

  // A deployment that offers no managed models has no catalog to list, and
  // never a refusal here: ModelsSection sends the catalog URLs to Auto
  // routing.
  if (status.data && !data.offered) return null;

  let body;
  const statusFailed = Boolean(status.error) && !status.data;
  if (statusFailed) {
    // The catalog waits for the status; one error, one Retry.
    body = (
      <GatewayEmptyState
        icon="catalog"
        title="Couldn't load the model catalog"
        description="The gateway did not say whether Future AGI models are available. Try again in a moment."
        actionLabel="Retry"
        onAction={() => status.refetch()}
      />
    );
  } else if (catalog.error) {
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
  } else if (rows && rows.length === 0) {
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
      />
    );
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
            <Collapse in={facetsOpen} unmountOnExit>
              <Box sx={{ pt: 1.5 }}>{panel}</Box>
            </Collapse>
          </Box>
        ) : (
          <Box
            sx={{
              width: FACET_PANEL_WIDTH,
              flexShrink: 0,
              minHeight: 0,
              overflowY: "auto",
              pr: 1,
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
              <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
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

          <GatewayGrid
            rowData={visible}
            loading={data.isLoading || !rows}
            columnDefs={columns}
            stateKey={CATALOG_LAYOUT_KEY}
            getRowId={(p) => p.data.id}
            rowHeight={52}
            defaultColDef={CATALOG_COL_DEF}
            onRowClicked={openModel}
            rowStyle={{ cursor: "pointer" }}
            tooltipShowDelay={400}
            emptyState={{
              variant: "no-results",
              title: "No models match",
              description:
                "Try a broader search, another type or provider, or fewer filters.",
              actionLabel: "Clear filters",
              onAction: clearFilters,
            }}
          />

          <Typography typography="s2" color="text.secondary" component="p">
            Prices are the Future AGI price (provider +{" "}
            {formatPercent(markupPercent)})
            {inclusivePrices
              ? ". Token prices are per 1M tokens; inclusive quotes use each stated billing unit. Inclusive requests are billed the provider's reported total plus the markup, not a sum of quoted dimensions. "
              : " per 1M tokens: what requests on Future AGI credits are billed. "}
            Requests through your own providers use your own provider keys.
          </Typography>
        </Box>
      </Box>
    );
  }

  return (
    <GatewayPageFrame
      title="Models"
      description="Every model in the gateway's catalog, what it costs, and how your organization can call it."
    >
      {!statusFailed && strip}
      {body}
    </GatewayPageFrame>
  );
}

import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";

import { GatewayGrid, GatewayStatusChip } from "../../components/frame";
import { migrateColumnState } from "../../components/frame/column-state";
import {
  APP_STATUS,
  appStatus,
  appToolSummary,
  authLabel,
  categoryLabel,
  currentProvider,
  tierBadge,
} from "../catalog-model";
import AppLogo from "./AppLogo";
import { AppStatusChip, TierBadge } from "./chips";

// An app not added yet sorts as available whether its cell shows Connect or
// Not added.
const STATUS_ORDER = {
  connected: 0,
  needs_key: 1,
  not_connected: 1,
  off: 2,
  available: 3,
  coming_soon: 4,
};
// The user's column order, widths, visibility and sort, kept in this browser.
// A layout saved before connected apps came first is carried over with every
// column it kept, Tier and Tools allowed included, but without its sort, so
// the new default (connected apps first) applies.
const STATE_KEY = "gateway-integrations-grid-v2";
const PREVIOUS_STATE_KEY = "gateway-integrations-grid";
const DROPPED_KEYS = new Set(["sort", "sortIndex"]);
const carriedLayout = (state) =>
  state
    .filter((col) => col && typeof col === "object")
    .map((col) =>
      Object.fromEntries(
        Object.entries(col).filter(([key]) => !DROPPED_KEYS.has(key)),
      ),
    );
const ROW_STYLE = { cursor: "pointer" };

// The name and second line keep one line each; at 1024-1440 they can end in
// an ellipsis, so each carries its full text as a title.
function AppCell({ data }) {
  const vendor = data.vendor?.name;
  const subtitle = vendor && vendor !== data.name ? vendor : data.description;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1.25,
        minWidth: 0,
        height: "100%",
      }}
    >
      <AppLogo app={data} size={28} />
      <Box sx={{ minWidth: 0, lineHeight: 1.3 }}>
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          noWrap
          title={data.name}
        >
          {data.name}
        </Typography>
        <Typography
          typography="s2"
          color="text.secondary"
          noWrap
          component="div"
          title={subtitle}
        >
          {subtitle}
        </Typography>
      </Box>
    </Box>
  );
}

AppCell.propTypes = { data: PropTypes.object.isRequired };

const centred = (children) => (
  <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
    {children}
  </Box>
);

function TierCell({ data }) {
  return centred(<TierBadge provider={currentProvider(data)} />);
}

TierCell.propTypes = { data: PropTypes.object.isRequired };

// How many of an app's tools the gateway allows; a muted dash, named for
// screen readers, where the app lists none.
function ToolsCell({ data }) {
  const { allowed, total } = appToolSummary(data);
  if (!total) {
    return centred(
      <Box
        component="span"
        role="img"
        aria-label="No tools listed"
        sx={{ color: "text.secondary" }}
      >
        —
      </Box>,
    );
  }
  return centred(`${allowed} of ${total}`);
}

ToolsCell.propTypes = { data: PropTypes.object.isRequired };

// The apps on the gateway grid (Future AGI's AG Grid theme, as Providers and
// Logs): sortable, resizable columns the user can shape, a row per app with
// its logo, connected apps first. Clicking a row, or Enter or Space on it,
// opens the app. With `canConnect` (an Owner or Admin, on a deployment that
// connects apps) an app not added yet has a quiet Connect button, which opens
// the same drawer (the drawer's Connect is the primary action); for everyone
// else it reads Not added. Below sm only the app and its status stay visible.
export default function IntegrationsGrid({
  apps,
  loading = false,
  canConnect = false,
  onOpen,
}) {
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("sm"));
  // Before the grid reads its saved layout (on grid ready).
  useState(() =>
    migrateColumnState(PREVIOUS_STATE_KEY, STATE_KEY, carriedLayout),
  );

  const columnDefs = useMemo(() => {
    // A closure, not cellRendererParams: the cell reads this render's
    // canConnect and onOpen. The grid ignores a row click that lands on the
    // button (GatewayGrid's fromControl), so the button opens the app once,
    // and Enter or Space on it belong to the button.
    function StatusCell({ data }) {
      const status = appStatus(data);
      if (status === APP_STATUS.AVAILABLE) {
        if (!canConnect) {
          return centred(
            <GatewayStatusChip tone="neutral" label="Not added" />,
          );
        }
        return centred(
          <Button
            size="small"
            variant="text"
            color="primary"
            aria-label={`Connect ${data.name}`}
            onClick={() => onOpen(data)}
          >
            Connect
          </Button>,
        );
      }
      return centred(<AppStatusChip status={status} />);
    }
    StatusCell.propTypes = { data: PropTypes.object.isRequired };

    return [
      {
        colId: "app",
        headerName: "App",
        field: "name",
        flex: 2.2,
        minWidth: 200,
        cellRenderer: AppCell,
      },
      {
        colId: "category",
        headerName: "Category",
        valueGetter: ({ data }) => categoryLabel(data.ee_category),
        tooltipValueGetter: ({ value }) => value || null,
        hide: narrow,
      },
      {
        colId: "auth",
        headerName: "Auth",
        valueGetter: ({ data }) => {
          const provider = currentProvider(data);
          return provider ? authLabel(provider.credential) : "";
        },
        tooltipValueGetter: ({ value }) => value || null,
        minWidth: 140,
        hide: narrow,
      },
      {
        colId: "tier",
        headerName: "Tier",
        valueGetter: ({ data }) =>
          tierBadge(currentProvider(data))?.label || "",
        cellRenderer: TierCell,
        hide: narrow,
      },
      {
        colId: "tools",
        headerName: "Tools allowed",
        valueGetter: ({ data }) => appToolSummary(data).allowed,
        valueFormatter: ({ data }) => {
          const { allowed, total } = appToolSummary(data);
          return total ? `${allowed} of ${total}` : "";
        },
        cellRenderer: ToolsCell,
        minWidth: 120,
        hide: narrow,
      },
      {
        colId: "status",
        headerName: "Status",
        valueGetter: ({ data }) => appStatus(data),
        comparator: (a, b) => STATUS_ORDER[a] - STATUS_ORDER[b],
        initialSort: "asc",
        cellRenderer: StatusCell,
        minWidth: 120,
      },
    ];
  }, [narrow, canConnect, onOpen]);

  const openRow = useCallback(({ data }) => onOpen(data), [onOpen]);
  const rowId = useCallback(({ data }) => data.id, []);

  return (
    <Box
      data-testid="integrations-grid"
      sx={{
        flex: "1 1 0",
        minHeight: 320,
        width: "100%",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <GatewayGrid
        rowData={loading ? undefined : apps}
        loading={loading}
        columnDefs={columnDefs}
        stateKey={STATE_KEY}
        getRowId={rowId}
        rowHeight={52}
        onRowClicked={openRow}
        rowStyle={ROW_STYLE}
      />
    </Box>
  );
}

IntegrationsGrid.propTypes = {
  apps: PropTypes.array.isRequired,
  loading: PropTypes.bool,
  canConnect: PropTypes.bool,
  onOpen: PropTypes.func.isRequired,
};

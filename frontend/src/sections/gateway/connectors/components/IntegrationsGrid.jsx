import React, { useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";

import { GatewayGrid } from "../../components/frame";
import {
  appStatus,
  appToolSummary,
  authLabel,
  categoryLabel,
  currentProvider,
  tierBadge,
} from "../catalog-model";
import AppLogo from "./AppLogo";
import { AppStatusChip, TierBadge } from "./chips";

const STATUS_ORDER = {
  connected: 0,
  needs_key: 1,
  not_connected: 1,
  off: 2,
  available: 3,
};
// The user's column order, widths, visibility and sort, kept in this browser.
const STATE_KEY = "gateway-integrations-grid";
const ROW_STYLE = { cursor: "pointer" };

function AppCell({ data }) {
  const vendor = data.vendor?.name;
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
        <Typography typography="s1" fontWeight="fontWeightMedium" noWrap>
          {data.name}
        </Typography>
        <Typography
          typography="s2"
          color="text.secondary"
          noWrap
          component="div"
        >
          {vendor && vendor !== data.name ? vendor : data.description}
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

function StatusCell({ data }) {
  return centred(<AppStatusChip status={appStatus(data)} />);
}

StatusCell.propTypes = { data: PropTypes.object.isRequired };

// The apps on the gateway grid (Future AGI's AG Grid theme, as Providers and
// Logs): sortable, resizable columns the user can shape, a row per app with
// its logo. Clicking a row, or Enter or Space on it, opens the app. Below sm
// only the app and its status stay visible.
export default function IntegrationsGrid({ apps, loading = false, onOpen }) {
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("sm"));

  const columnDefs = useMemo(
    () => [
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
        hide: narrow,
      },
      {
        colId: "auth",
        headerName: "Auth",
        valueGetter: ({ data }) => {
          const provider = currentProvider(data);
          return provider ? authLabel(provider.credential) : "";
        },
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
        minWidth: 120,
        hide: narrow,
      },
      {
        colId: "status",
        headerName: "Status",
        valueGetter: ({ data }) => appStatus(data),
        comparator: (a, b) => STATUS_ORDER[a] - STATUS_ORDER[b],
        cellRenderer: StatusCell,
        minWidth: 120,
      },
    ],
    [narrow],
  );

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
  onOpen: PropTypes.func.isRequired,
};

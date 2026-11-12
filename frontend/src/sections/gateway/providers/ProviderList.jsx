import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import { useResponsive } from "src/hooks/use-responsive";

import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayStatusChip,
} from "../components/frame";
import ConfirmDialog from "../components/ConfirmDialog";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import AddProviderDialog from "./AddProviderDialog";
import ProviderDetailDrawer from "./ProviderDetailDrawer";
import {
  isSavedButNotApplied,
  useRemoveProvider,
} from "./hooks/useGatewayConfig";
import { fromControl } from "./grid-events";
import FaIcon from "./ui/FaIcon";
import {
  endpointLabel,
  formatCount,
  formatErrorRate,
  formatLatency,
  matchesProviderSearch,
  PROVIDER_STATUS,
  providerStatusRule,
} from "./provider-rows";
import { GATEWAY_MONO } from "../utils/fonts";
import { typography } from "src/theme/typography";

// A grid cell's monospace text at the FA s2 token (cellStyle is plain CSS).
const MONO_CELL = {
  fontFamily: GATEWAY_MONO,
  fontSize: typography.s2.fontSize,
};

function ProviderCell({ data }) {
  return (
    <Box sx={{ minWidth: 0, lineHeight: 1.3, py: 0.5 }}>
      <Typography typography="s1" fontWeight="fontWeightMedium" noWrap>
        {data.displayName}
      </Typography>
      {data.displayName !== data.name && (
        <Typography typography="s2" color="text.secondary" noWrap>
          {data.name}
        </Typography>
      )}
    </Box>
  );
}

ProviderCell.propTypes = { data: PropTypes.object.isRequired };

// The status the providers read derives from the org's own requests, with
// the reason it gives (for example "12.5% of 40 requests failed in 24 h").
function StatusCell({ data }) {
  const status = PROVIDER_STATUS[data.status] || PROVIDER_STATUS.unknown;
  return (
    <Box
      sx={{
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        justifyContent: "center",
        height: "100%",
        gap: 0.25,
      }}
    >
      <GatewayStatusChip tone={status.tone} label={status.label} />
      {data.statusReason && (
        <Typography
          typography="s3"
          color="text.secondary"
          noWrap
          title={data.statusReason}
          sx={{ maxWidth: "100%" }}
        >
          {data.statusReason}
        </Typography>
      )}
    </Box>
  );
}

StatusCell.propTypes = { data: PropTypes.object.isRequired };

// Edit and Remove change the org's gateway config: Owner or Admin only. For
// anyone else they show disabled and say why (GatewayAdminOnly).
function ActionsCell({ data, context }) {
  const stop = (fn) => (event) => {
    event.stopPropagation();
    fn(data);
  };
  const { canWrite } = context;
  const action = (label, icon, onClick) => {
    const button = (
      <IconButton
        size="small"
        aria-label={`${label} provider ${data.name}`}
        onClick={stop(onClick)}
        disabled={!canWrite}
      >
        <FaIcon name={icon} />
      </IconButton>
    );
    return canWrite ? (
      <Tooltip title={label}>{button}</Tooltip>
    ) : (
      <GatewayAdminOnly allowed={false}>{button}</GatewayAdminOnly>
    );
  };
  return (
    <Box
      sx={{ display: "flex", alignItems: "center", height: "100%", gap: 0.5 }}
    >
      {action("Edit", "edit", context.onEdit)}
      {action("Remove", "delete", context.onRemove)}
    </Box>
  );
}

ActionsCell.propTypes = {
  data: PropTypes.object.isRequired,
  context: PropTypes.shape({
    canWrite: PropTypes.bool,
    onEdit: PropTypes.func.isRequired,
    onRemove: PropTypes.func.isRequired,
  }).isRequired,
};

const NUMERIC = { type: "rightAligned", maxWidth: 180, minWidth: 130 };

// The org's providers on one AG Grid: who, its status and where, which
// format, how many models, and the last 24 hours of the org's own traffic.
// A row opens its detail; Edit and Remove sit on the row, enabled for an
// Owner or Admin. `managedOffered` false: the deployment offers no managed
// models, and the status rule says nothing about them. `orgProviders` is the
// providers read the model pages use, so the detail links only the models
// that have a page.
export default function ProviderList({
  rows,
  loading,
  gatewayId,
  canWrite,
  onAdd,
  managedOffered = true,
  orgProviders,
}) {
  const [search, setSearch] = useState("");
  const [openId, setOpenId] = useState(null);
  const [editTarget, setEditTarget] = useState(null);
  const [removeTarget, setRemoveTarget] = useState(null);
  const removeProvider = useRemoveProvider();
  const isPhone = useResponsive("down", "sm");

  const shown = useMemo(
    () =>
      rows ? rows.filter((row) => matchesProviderSearch(row, search)) : rows,
    [rows, search],
  );
  const openRow = useMemo(
    () => (rows || []).find((row) => row.id === openId) || null,
    [rows, openId],
  );

  const handleEdit = useCallback((row) => {
    setEditTarget({ name: row.name, config: row.config });
  }, []);
  const handleRemove = useCallback((row) => setRemoveTarget(row), []);

  const columnDefs = useMemo(
    () => [
      {
        colId: "provider",
        headerName: "Provider",
        field: "displayName",
        cellRenderer: ProviderCell,
        minWidth: 220,
        flex: 2,
      },
      {
        colId: "status",
        headerName: "Status (24 h)",
        field: "status",
        cellRenderer: StatusCell,
        minWidth: 200,
        flex: 2,
      },
      {
        colId: "endpoint",
        headerName: "Endpoint",
        valueGetter: ({ data }) => endpointLabel(data?.baseUrl),
        minWidth: 200,
        flex: 2,
        cellStyle: MONO_CELL,
      },
      {
        colId: "format",
        headerName: "Format",
        field: "apiFormat",
        maxWidth: 140,
      },
      {
        colId: "models",
        headerName: "Models",
        valueGetter: ({ data }) => data?.models?.length ?? 0,
        ...NUMERIC,
        minWidth: 100,
        maxWidth: 120,
      },
      {
        colId: "requests",
        headerName: "Requests (24 h)",
        field: "requests",
        valueFormatter: ({ value }) => formatCount(value),
        ...NUMERIC,
        minWidth: 150,
      },
      {
        colId: "latency",
        headerName: "Avg latency",
        field: "avgLatencyMs",
        valueFormatter: ({ value }) => formatLatency(value),
        ...NUMERIC,
      },
      {
        colId: "errors",
        headerName: "Error rate",
        field: "errorRate",
        valueFormatter: ({ value }) => formatErrorRate(value),
        ...NUMERIC,
      },
      {
        colId: "actions",
        headerName: "",
        cellRenderer: ActionsCell,
        sortable: false,
        resizable: false,
        width: 96,
        minWidth: 96,
        maxWidth: 96,
        flex: 0,
        // At phone width a pinned column eats the little room there is.
        pinned: isPhone ? undefined : "right",
      },
    ],
    [isPhone],
  );

  const gridContext = useMemo(
    () => ({
      canWrite: Boolean(canWrite),
      onEdit: handleEdit,
      onRemove: handleRemove,
    }),
    [canWrite, handleEdit, handleRemove],
  );

  // A removal the gateway did not apply is still stored: useRemoveProvider
  // reports it once, as a warning, and neither toast here runs for it; the
  // dialog closes as it does after a removal.
  const confirmRemove = () => {
    const target = removeTarget;
    const close = () => {
      setRemoveTarget(null);
      if (openId === target.id) setOpenId(null);
    };
    removeProvider.mutate(
      { gatewayId, name: target.name },
      {
        onSuccess: () => {
          enqueueSnackbar(`Provider "${target.displayName}" removed`, {
            variant: "success",
          });
          close();
        },
        onError: () => {
          enqueueSnackbar("Failed to remove provider", { variant: "error" });
        },
        onSettled: (_result, error) => {
          if (isSavedButNotApplied(error)) close();
        },
      },
    );
  };

  const isEmpty = !loading && Array.isArray(rows) && rows.length === 0;

  return (
    <>
      {isEmpty ? (
        <GatewayEmptyState
          icon="providers"
          title="No providers yet"
          description="Add a provider with your own API key to call its models through the gateway."
          actionLabel={canWrite ? "Add provider" : undefined}
          onAction={canWrite ? onAdd : undefined}
        />
      ) : (
        <>
          <GatewayFilterBar
            search={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search provider, endpoint or model"
            actions={
              <Typography typography="s2" color="text.secondary">
                Status, requests, latency and error rate: last 24 h, from your
                own requests
              </Typography>
            }
          />
          <Typography typography="s2" color="text.secondary">
            {providerStatusRule({ managedOffered })}
          </Typography>
          <GatewayGrid
            rowData={shown}
            loading={loading}
            columnDefs={columnDefs}
            context={gridContext}
            stateKey="gateway-providers"
            getRowId={(p) => p.data.id}
            onRowClicked={(event) => {
              // A click on Edit or Remove is not a click on the row.
              if (fromControl(event)) return;
              setOpenId(event.data.id);
            }}
            rowHeight={56}
            rowStyle={{ cursor: "pointer" }}
            emptyState={{
              variant: "no-results",
              onAction: () => setSearch(""),
            }}
          />
        </>
      )}

      <ProviderDetailDrawer
        row={openRow}
        onClose={() => setOpenId(null)}
        canWrite={canWrite}
        onEdit={handleEdit}
        onRemove={handleRemove}
        orgProviders={orgProviders}
      />

      <ConfirmDialog
        open={Boolean(removeTarget)}
        onClose={() => setRemoveTarget(null)}
        title="Remove provider"
        message={
          removeTarget
            ? `Remove "${removeTarget.displayName}"? Its stored key is deleted and requests for its models stop routing to it once the gateway reloads.`
            : ""
        }
        typeToConfirm={removeTarget?.name || ""}
        confirmLabel="Remove"
        confirmColor="error"
        isLoading={removeProvider.isPending}
        onConfirm={confirmRemove}
      />

      <AddProviderDialog
        open={Boolean(editTarget)}
        onClose={() => setEditTarget(null)}
        gatewayId={gatewayId}
        provider={editTarget}
      />
    </>
  );
}

ProviderList.propTypes = {
  rows: PropTypes.array,
  loading: PropTypes.bool,
  gatewayId: PropTypes.string,
  canWrite: PropTypes.bool,
  onAdd: PropTypes.func,
  managedOffered: PropTypes.bool,
  orgProviders: PropTypes.array,
};

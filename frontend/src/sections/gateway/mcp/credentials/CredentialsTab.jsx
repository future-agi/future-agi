import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayStatusChip,
} from "../../components/frame";
import ConfirmDialog from "../../components/ConfirmDialog";
import { useGatewayConfig } from "../../providers/hooks/useGatewayConfig";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import { formatDateTime } from "../../utils/formatters";
import { apiErrorMessage } from "../api";
import {
  useMCPCredentials,
  useRevokeMCPCredential,
} from "../hooks/useMCPCredentials";
import FilterSelect from "../components/FilterSelect";
import McpIcon from "../components/McpIcon";
import {
  AddCredentialDialog,
  RotateCredentialDialog,
} from "./CredentialDialogs";
import {
  STATUS_OPTIONS,
  headerLabel,
  kindLabel,
  serversWithActiveCredential,
  statusOf,
} from "./credentials";

const ROW_HEIGHT = 40;

function StatusCell({ value }) {
  const status = statusOf(value);
  return <GatewayStatusChip tone={status.tone} label={status.label} />;
}

StatusCell.propTypes = { value: PropTypes.string };

function ActionsCell({ data, context }) {
  if (data.status !== "active") return null;
  return (
    <Box
      sx={{ display: "flex", gap: 0.5, height: "100%", alignItems: "center" }}
    >
      <Button
        size="small"
        variant="text"
        disabled={!context.canEdit}
        onClick={(event) => {
          event.stopPropagation();
          context.onRotate(data);
        }}
      >
        Rotate
      </Button>
      <Button
        size="small"
        variant="text"
        disabled={!context.canEdit}
        color="error"
        onClick={(event) => {
          event.stopPropagation();
          context.onRevoke(data);
        }}
      >
        Revoke
      </Button>
    </Box>
  );
}

ActionsCell.propTypes = { data: PropTypes.object, context: PropTypes.object };

// The org's MCP credential vault: the tokens and API keys the gateway sends
// to upstream MCP servers. Values are write-only; only an org owner or admin
// may add, rotate or revoke one.
export default function CredentialsTab({ gatewayId }) {
  const { canEdit, disabledReason } = useGatewayAdmin();
  const { data: config, isLoading: configLoading } =
    useGatewayConfig(gatewayId);
  const {
    data,
    isLoading: vaultLoading,
    isError,
    refetch,
  } = useMCPCredentials();
  // The empty state and the Add button depend on the org's servers too.
  const isLoading = vaultLoading || configLoading;
  const revoke = useRevokeMCPCredential();
  const [search, setSearch] = useState("");
  const [serverId, setServerId] = useState("");
  const [status, setStatus] = useState("");
  const [adding, setAdding] = useState(false);
  const [rotating, setRotating] = useState(null);
  const [revoking, setRevoking] = useState(null);

  const rows = useMemo(() => data?.rows || [], [data]);
  const serverIds = useMemo(
    () => Object.keys(config?.mcp?.servers || {}).sort(),
    [config],
  );
  const taken = useMemo(() => serversWithActiveCredential(rows), [rows]);

  const shown = useMemo(() => {
    const term = search.trim().toLowerCase();
    return rows.filter(
      (row) =>
        (!serverId || row.server_id === serverId) &&
        (!status || row.status === status) &&
        (!term ||
          [row.server_id, row.label, row.header_name, row.kind]
            .join(" ")
            .toLowerCase()
            .includes(term)),
    );
  }, [rows, search, serverId, status]);

  const columnDefs = useMemo(
    () => [
      {
        headerName: "Server",
        colId: "server",
        field: "server_id",
        minWidth: 140,
      },
      {
        headerName: "Kind",
        colId: "kind",
        field: "kind",
        valueFormatter: ({ value }) => kindLabel(value),
        minWidth: 120,
        flex: 0.8,
      },
      {
        headerName: "Sent in",
        colId: "header",
        valueGetter: ({ data: row }) => headerLabel(row),
        minWidth: 160,
      },
      {
        headerName: "Label",
        colId: "label",
        field: "label",
        valueFormatter: ({ value }) => value || "—",
        minWidth: 130,
      },
      {
        headerName: "Status",
        colId: "status",
        field: "status",
        cellRenderer: StatusCell,
        minWidth: 130,
        flex: 0.8,
      },
      {
        headerName: "Version",
        colId: "version",
        field: "version",
        minWidth: 90,
        flex: 0.5,
      },
      {
        headerName: "Created",
        colId: "created",
        field: "created_at",
        valueFormatter: ({ value }) => formatDateTime(value),
        minWidth: 170,
      },
      {
        headerName: "Rotated",
        colId: "rotated",
        field: "rotated_at",
        valueFormatter: ({ value }) => formatDateTime(value),
        minWidth: 170,
      },
      {
        headerName: "Last used",
        colId: "used",
        field: "last_used_at",
        valueFormatter: ({ value }) => formatDateTime(value),
        minWidth: 170,
      },
      {
        headerName: "",
        colId: "actions",
        cellRenderer: ActionsCell,
        sortable: false,
        resizable: false,
        minWidth: 150,
        maxWidth: 160,
        pinned: "right",
      },
    ],
    [],
  );

  const context = useMemo(
    () => ({ canEdit, onRotate: setRotating, onRevoke: setRevoking }),
    [canEdit],
  );

  const confirmRevoke = async () => {
    if (!canEdit || !revoking || revoke.isPending) return;
    const target = revoking;
    try {
      const result = await revoke.mutateAsync(target.id);
      if (result?.gateway_synced === false) {
        enqueueSnackbar(
          "Revocation saved in the vault, but gateway removal is unconfirmed. The gateway may still send this credential until a successful sync. Use Reload config to retry synchronization; do not revoke it again.",
          { variant: "warning" },
        );
      } else {
        enqueueSnackbar(`Credential for ${target.server_id} revoked`, {
          variant: "success",
        });
      }
    } catch (err) {
      enqueueSnackbar(
        apiErrorMessage(err, "The credential could not be revoked."),
        {
          variant: "error",
        },
      );
    }
    setRevoking(null);
  };

  const filtered = Boolean(search || serverId || status);
  const canAdd = canEdit && serverIds.length > 0;
  const openAdd = () => {
    if (canAdd) setAdding(true);
  };

  let body;
  if (isError) {
    body = (
      <GatewayEmptyState
        icon="monitoring"
        title="Credentials could not be loaded"
        description="The credential vault did not answer. Try again."
        actionLabel="Try again"
        onAction={() => refetch()}
      />
    );
  } else if (!isLoading && rows.length === 0) {
    body = (
      <GatewayEmptyState
        icon="keys"
        title="No stored credentials"
        description={
          serverIds.length > 0
            ? "Tokens and API keys for servers that need one are kept here, encrypted, never in the config, and never shown again. None of this organization's servers has one yet."
            : "Add an MCP server first. Tokens and API keys it needs are kept here, encrypted, and never shown again."
        }
        actionLabel="Add credential"
        onAction={openAdd}
        actionDisabled={!canAdd}
      />
    );
  } else if (!isLoading && shown.length === 0 && filtered) {
    body = (
      <GatewayEmptyState
        variant="no-results"
        title="No credentials match"
        actionLabel="Clear filters"
        onAction={() => {
          setSearch("");
          setServerId("");
          setStatus("");
        }}
      />
    );
  } else {
    body = (
      <GatewayGrid
        rowData={isLoading ? undefined : shown}
        loading={isLoading}
        columnDefs={columnDefs}
        context={context}
        stateKey="gateway-mcp-credentials"
        getRowId={(params) => params.data.id}
        height={Math.min(
          Math.max(ROW_HEIGHT * Math.max(shown.length, 3) + 42, 200),
          560,
        )}
      />
    );
  }

  const empty = !isLoading && !isError && rows.length === 0;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      {!canEdit && (
        <Typography color="text.secondary">{disabledReason}</Typography>
      )}
      {!empty && (
        <GatewayFilterBar
          search={search}
          onSearchChange={setSearch}
          searchPlaceholder="Search server or label"
          actions={
            <Button
              size="small"
              variant="contained"
              color="primary"
              startIcon={<McpIcon name="plus" size={16} />}
              onClick={openAdd}
              disabled={!canAdd}
            >
              Add credential
            </Button>
          }
        >
          <FilterSelect
            label="Server"
            value={serverId}
            onChange={setServerId}
            options={serverIds.map((id) => ({ value: id, label: id }))}
            allLabel="All servers"
          />
          <FilterSelect
            label="Status"
            value={status}
            onChange={setStatus}
            options={STATUS_OPTIONS}
          />
        </GatewayFilterBar>
      )}

      {body}

      {data?.count > rows.length && (
        <Typography typography="s2" color="text.secondary">
          {`Showing the first ${rows.length} of ${data.count} credentials.`}
        </Typography>
      )}

      <AddCredentialDialog
        open={adding}
        onClose={() => setAdding(false)}
        serverIds={serverIds}
        taken={taken}
      />
      <RotateCredentialDialog
        credential={rotating}
        onClose={() => setRotating(null)}
      />
      <ConfirmDialog
        open={canEdit && Boolean(revoking)}
        onClose={() => setRevoking(null)}
        onConfirm={confirmRevoke}
        title="Revoke credential"
        message={
          revoking
            ? `This revokes the credential in the vault. The gateway stops sending this credential to ${revoking.server_id} only after a successful sync. Calls that need it will then fail until a new one is stored. This does not invalidate the token at its vendor.`
            : ""
        }
        confirmLabel="Revoke credential"
        isLoading={revoke.isPending}
      />
    </Box>
  );
}

CredentialsTab.propTypes = {
  gatewayId: PropTypes.string,
};

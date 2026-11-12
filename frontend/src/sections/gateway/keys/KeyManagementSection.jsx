import { getGatewayAuthenticationErrorMessage } from "src/utils/errorUtils";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
import { useNavigate, useSearchParams } from "react-router-dom";
import { enqueueSnackbar } from "notistack";

import { useResponsive } from "src/hooks/use-responsive";
import { relativeTime } from "src/utils/format-time";

import { useOrganization } from "src/contexts/OrganizationContext";
import ChipSelector from "src/components/chip-selector/ChipSelector";

import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayPageFrame,
  GatewayStatusChip,
} from "../components/frame";
import ConfirmDialog from "../components/ConfirmDialog";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayContext } from "../context/useGatewayContext";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import { formatDate } from "../utils/formatters";
import CreditBalanceChip from "../providers/ui/CreditBalanceChip";
import { fromControl } from "../providers/grid-events";
import FaIcon from "../providers/ui/FaIcon";
import CreateKeyDialog from "./CreateKeyDialog";
import EditKeyDialog from "./EditKeyDialog";
import KeyDetailDrawer from "./KeyDetailDrawer";
import OneTimeKeyReveal from "./OneTimeKeyReveal";
import { useKeyRevocation } from "./useKeyRevocation";
import {
  canRotate,
  ROTATE_DIALOG_MESSAGE,
  ROTATE_REVOCATION_COPY,
} from "./rotate";
import {
  useApiKeys,
  useRevokeApiKey,
  useRotateApiKey,
  useSyncApiKeys,
} from "./hooks/useApiKeys";
import {
  KEY_STATUS,
  KEY_STATUS_FILTERS,
  accessLabel,
  exactTime,
  keyCountSummary,
  keyPrefixLabel,
  liveKeysFirst,
  matchesKeySearch,
} from "./key-format";
import { GATEWAY_MONO } from "../utils/fonts";
import {
  PLACEHOLDER_REQUEST_NOTE,
  snippetBaseUrl,
} from "../utils/snippetBaseUrl";
import { typography } from "src/theme/typography";

// A grid cell's monospace text at the FA s2 token (cellStyle is plain CSS).
const MONO_CELL = {
  fontFamily: GATEWAY_MONO,
  fontSize: typography.s2.fontSize,
};

function NameCell({ data, context }) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        height: "100%",
        minWidth: 0,
      }}
    >
      <Button
        size="small"
        title={data.name}
        aria-label={`View details for ${data.name}`}
        onClick={(event) => {
          event.stopPropagation();
          context?.onOpen(data);
        }}
        sx={{
          textTransform: "none",
          justifyContent: "flex-start",
          minWidth: 0,
          "&.Mui-focusVisible": {
            outline: (theme) => `2px solid ${theme.palette.primary.main}`,
            outlineOffset: 2,
          },
        }}
      >
        {data.name}
      </Button>
    </Box>
  );
}
NameCell.propTypes = {
  data: PropTypes.object.isRequired,
  context: PropTypes.object,
};

function StatusCell({ data }) {
  const status = KEY_STATUS[data.status];
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      {status ? (
        <GatewayStatusChip tone={status.tone} label={status.label} />
      ) : (
        data.status
      )}
    </Box>
  );
}
StatusCell.propTypes = { data: PropTypes.object.isRequired };

// Edit remains administrative; Rotate additionally requires personal ownership.
function ActionsCell({ data, context }) {
  const [anchor, setAnchor] = useState(null);
  if (!["active", "pending", "failed"].includes(data.status)) return null;
  const canRevoke = context.canRevoke(data);
  const canAct = context.canWrite || canRevoke;
  const run = (allowed, fn) => (event) => {
    event.stopPropagation();
    setAnchor(null);
    if (allowed) fn(data);
  };
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <GatewayAdminOnly allowed={Boolean(canAct)}>
        <IconButton
          size="small"
          aria-label={`Actions for ${data.name}`}
          aria-haspopup="menu"
          disabled={!canAct}
          onClick={(event) => {
            event.stopPropagation();
            setAnchor(event.currentTarget);
          }}
        >
          <FaIcon name="more" />
        </IconButton>
      </GatewayAdminOnly>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={(event) => {
          event?.stopPropagation?.();
          setAnchor(null);
        }}
        onClick={(event) => event.stopPropagation()}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        {data.status === "active" && (
          <MenuItem
            disabled={!context.canWrite}
            onClick={run(context.canWrite, context.onEdit)}
          >
            Edit
          </MenuItem>
        )}
        {canRotate(data, context.userId) && canRevoke && (
          <MenuItem
            disabled={!context.canWrite}
            onClick={run(context.canWrite, context.onRotate)}
          >
            Rotate
          </MenuItem>
        )}
        <MenuItem
          disabled={!canRevoke}
          onClick={run(canRevoke, context.onRevoke)}
          sx={{ color: "error.main" }}
        >
          Revoke
        </MenuItem>
      </Menu>
    </Box>
  );
}
ActionsCell.propTypes = {
  data: PropTypes.object.isRequired,
  context: PropTypes.object.isRequired,
};

// The API's error text (Django answers { result: "..." }) or axios's.
function readError(error) {
  const remedy = getGatewayAuthenticationErrorMessage(error);
  if (remedy) return remedy;
  if (typeof error?.result === "string") return error.result;
  return error?.message || "";
}

// A time cell: the date (for Last used, how long ago), with the exact
// instant and its zone on hover. The column still sorts by the stored time.
function timeCell(read, empty) {
  function TimeCell({ value }) {
    if (!value) return empty;
    return (
      <Box component="span" title={exactTime(value)}>
        {read(value)}
      </Box>
    );
  }
  TimeCell.propTypes = { value: PropTypes.string };
  return TimeCell;
}
const CreatedCell = timeCell(formatDate, "—");
const LastUsedCell = timeCell(relativeTime, "Never");
const ExpiresCell = timeCell(formatDate, "Never");

function rotateNotes(name, result) {
  const notes = [];
  if (result.oldRevoked) {
    notes.push({
      severity: "info",
      text: `${name}'s old-key revocation was requested; update your apps to use the new key, and do not assume the old key has stopped working.`,
    });
    notes.push({
      severity: "info",
      text: ROTATE_REVOCATION_COPY.local(name),
    });
    const enforcementConfirmed =
      result.gatewayRevoked === true && !result.revokeWarning;
    notes.push({
      severity: enforcementConfirmed ? "info" : "warning",
      text: enforcementConfirmed
        ? ROTATE_REVOCATION_COPY.confirmed
        : ROTATE_REVOCATION_COPY.pending,
    });
    if (result.revokeWarning) {
      notes.push({ severity: "warning", text: result.revokeWarning });
    }
  } else {
    notes.push({
      severity: "warning",
      text: "Revocation of the old key could not be confirmed. Check its status and revoke it if needed; update your apps to use the new key.",
    });
  }
  if (!result.expiryCopied) {
    notes.push({
      severity: "warning",
      text: "The old key's expiry could not be confirmed on the new key. Check the new key's expiry and edit it if needed.",
    });
  }
  // Preserve partial-success guidance and add the operator remedy when either
  // later gateway operation failed authentication. Never show upstream text.
  const authenticationWarnings = [
    result.expiryWarning,
    !result.oldRevoked && result.revokeWarning,
  ].filter(Boolean);
  [...new Set(authenticationWarnings)].forEach((text) => {
    notes.push({ severity: "warning", text });
  });
  return notes;
}

// Providers & keys > API keys: the keys apps use to call the gateway, on the
// gateway page frame. Personal creation belongs to every active org member,
// including Viewers. Configuration controls retain their admin gate;
// the server checks live membership and authenticated session provenance.
export default function KeyManagementSection() {
  const { canEdit: canWrite } = useGatewayAdmin();
  const { canRevoke, userId, organizationId } = useKeyRevocation();
  const { orgLevel, currentOrganizationId, isReady } = useOrganization();
  const canCreate = Boolean(
    isReady &&
      currentOrganizationId &&
      Number.isFinite(orgLevel) &&
      orgLevel >= 1,
  );
  const {
    gatewayId, gateway, isLoading: gatewayLoading, hosted, deploymentModeConfirmed,
  } = useGatewayContext();
  const requestEndpoint = snippetBaseUrl(gateway?.baseUrl, {
    hosted, deploymentModeConfirmed,
  });
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  const [openId, setOpenId] = useState(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState(null);
  const [rotateTarget, setRotateTarget] = useState(null);
  const [revokeTarget, setRevokeTarget] = useState(null);
  const [revokeWarning, setRevokeWarning] = useState(null);
  const detailRef = useRef(null);
  const [reveal, setReveal] = useState(null);

  // The page shows its own error state for this read, so no app-wide toast.
  const keysQuery = useApiKeys(gatewayId, { meta: { errorHandled: true } });
  const syncMutation = useSyncApiKeys();
  const revokeMutation = useRevokeApiKey();
  const rotateMutation = useRotateApiKey();
  const isPhone = useResponsive("down", "sm");

  // /dashboard/gateway/keys?create=1 opens Create key (the public model
  // pages link here); the parameter is dropped once the dialog closes.
  const createRequested = searchParams.get("create") === "1";
  const createShown = canCreate && (createOpen || createRequested);
  const closeCreate = useCallback(() => {
    setCreateOpen(false);
    if (createRequested) {
      const next = new URLSearchParams(searchParams);
      next.delete("create");
      const query = next.toString();
      // No pathname: the URL stays on this page, only the query changes.
      navigate({ search: query ? `?${query}` : "" }, { replace: true });
    }
  }, [createRequested, navigate, searchParams]);

  // Preserve the link while membership loads; unavailable membership never
  // opens a dialog. A later org switch/deactivation also closes a local open.
  useEffect(() => {
    if (isReady && !canCreate) closeCreate();
  }, [canCreate, closeCreate, isReady]);
  useEffect(() => {
    setCreateOpen(false);
  }, [currentOrganizationId]);

  const keys = keysQuery.data;
  const rows = useMemo(() => {
    if (!keys) return undefined;
    return liveKeysFirst(
      keys.filter(
        (key) =>
          (!statusFilter || key.status === statusFilter) &&
          matchesKeySearch(key, search),
      ),
    );
  }, [keys, statusFilter, search]);

  const handleSync = () => {
    if (!gatewayId || !canWrite || syncMutation.isPending) return;
    syncMutation.mutate(gatewayId, {
      onSuccess: (result) => {
        enqueueSnackbar(`Synced ${result?.synced ?? 0} keys from the gateway`, {
          variant: "success",
        });
      },
      onError: (error) => {
        enqueueSnackbar(
          getGatewayAuthenticationErrorMessage(error) ||
            "Failed to sync keys from the gateway",
          {
            variant: "error",
          },
        );
      },
    });
  };

  const requestRevoke = useCallback(
    (key) => {
      if (canRevoke(key)) {
        setRevokeTarget({
          ...key,
          requestedBy: userId,
          requestedIn: organizationId,
        });
      }
    },
    [canRevoke, userId, organizationId],
  );

  const confirmRevoke = () => {
    const target = revokeTarget;
    // A dialog may outlive an identity, role, organization or key-data change.
    // Re-evaluate current server IDs and membership, not the opening-time grant.
    const currentKey = keys?.find((key) => key.id === target?.id);
    if (
      !target ||
      target.requestedBy !== userId ||
      target.requestedIn !== organizationId ||
      !canRevoke(currentKey) ||
      keysQuery.isError ||
      revokeMutation.isPending
    )
      return;
    revokeMutation.mutate(target.id, {
      onSuccess: (result) => {
        enqueueSnackbar(result?.warning || `Key "${target.name}" revoked`, {
          variant: result?.warning ? "warning" : "success",
        });
        setRevokeWarning(
          result?.warning
            ? { keyId: target.id, message: result.warning }
            : null,
        );
        setOpenId(result?.warning ? target.id : null);
        setRevokeTarget(null);
      },
      onError: (error) => {
        enqueueSnackbar(
          getGatewayAuthenticationErrorMessage(error) ||
            `Failed to revoke "${target.name}"`,
          {
            variant: "error",
          },
        );
      },
    });
  };

  const requestRotate = useCallback(
    (key) => {
      if (canWrite && canRotate(key, userId) && canRevoke(key)) {
        setRotateTarget({
          ...key,
          requestedBy: userId,
          requestedIn: organizationId,
        });
      }
    },
    [canWrite, canRevoke, userId, organizationId],
  );

  const confirmRotate = () => {
    const target = rotateTarget;
    const currentKey = keys?.find((key) => key.id === target?.id);
    if (
      !canWrite ||
      !target ||
      target.requestedBy !== userId ||
      target.requestedIn !== organizationId ||
      !canRotate(currentKey, userId) ||
      !canRevoke(currentKey) ||
      keysQuery.isError ||
      rotateMutation.isPending
    )
      return;
    rotateMutation.mutate(currentKey, {
      onSuccess: (result) => {
        setRotateTarget(null);
        setOpenId(null);
        setReveal({
          name: result.created?.name || target.name,
          secret: result.secret,
          id: result.created?.id,
          gatewayKeyId:
            result.created?.gatewayKeyId ?? result.created?.gateway_key_id,
          allowedModels:
            result.created?.allowedModels ?? result.created?.allowed_models ??
            target.allowedModels ?? target.allowed_models,
          allowedProviders:
            result.created?.allowedProviders ?? result.created?.allowed_providers ??
            target.allowedProviders ?? target.allowed_providers,
          requestedModels: target.allowedModels ?? target.allowed_models,
          requestedProviders: target.allowedProviders ?? target.allowed_providers,
          notes: rotateNotes(target.name, result),
        });
      },
      onError: (err) => {
        enqueueSnackbar(
          getGatewayAuthenticationErrorMessage(err) ||
            `Rotation could not be confirmed: ${err?.message || "the new key could not be retrieved"}. Check the key list before retrying.`,
          { variant: "error" },
        );
      },
    });
  };

  const gridContext = useMemo(
    () => ({
      canWrite,
      canRevoke,
      userId,
      onOpen: (key) => setOpenId(key.id),
      onEdit: (key) => setEditTarget(key),
      onRotate: requestRotate,
      onRevoke: requestRevoke,
    }),
    [canWrite, canRevoke, userId, requestRotate, requestRevoke],
  );

  const columnDefs = useMemo(
    () => [
      {
        colId: "name",
        headerName: "Name",
        field: "name",
        cellRenderer: NameCell,
        // Room goes to the Key column first; a longer name has its title.
        minWidth: 150,
        flex: 1.5,
        // Below 1440 the columns scroll sideways; the name stays in view.
        pinned: isPhone ? undefined : "left",
      },
      {
        // The whole two-ended prefix, "sk-agentcc-4f2a…9c1d", at its
        // narrowest: the tail is what tells two keys apart.
        colId: "key",
        headerName: "Key",
        valueGetter: ({ data }) => keyPrefixLabel(data?.keyPrefix),
        minWidth: 180,
        cellStyle: MONO_CELL,
      },
      {
        colId: "status",
        headerName: "Status",
        field: "status",
        cellRenderer: StatusCell,
        minWidth: 120,
        maxWidth: 140,
      },
      {
        colId: "owner",
        headerName: "Owner",
        field: "owner",
        valueFormatter: ({ value }) => value || "—",
        minWidth: 110,
        tooltipValueGetter: ({ value }) => value || undefined,
      },
      {
        colId: "models",
        headerName: "Models",
        valueGetter: ({ data }) =>
          accessLabel(data?.allowedModels, "All models"),
        minWidth: 110,
        tooltipValueGetter: ({ value }) => value,
      },
      {
        colId: "providers",
        headerName: "Providers",
        valueGetter: ({ data }) =>
          accessLabel(data?.allowedProviders, "All providers"),
        minWidth: 120,
        tooltipValueGetter: ({ value }) => value,
      },
      {
        colId: "created",
        headerName: "Created",
        field: "created_at",
        cellRenderer: CreatedCell,
        minWidth: 110,
      },
      {
        colId: "lastUsed",
        headerName: "Last used",
        field: "last_used_at",
        cellRenderer: LastUsedCell,
        minWidth: 110,
      },
      {
        colId: "expires",
        headerName: "Expires",
        field: "expires_at",
        cellRenderer: ExpiresCell,
        minWidth: 110,
      },
      {
        colId: "actions",
        headerName: "",
        cellRenderer: ActionsCell,
        sortable: false,
        resizable: false,
        width: 64,
        minWidth: 64,
        maxWidth: 64,
        flex: 0,
        // At phone width a pinned column eats the little room there is.
        pinned: isPhone ? undefined : "right",
      },
    ],
    [isPhone],
  );

  const loading = gatewayLoading || keysQuery.isLoading;
  const failed = keysQuery.isError;
  const noKeys =
    !loading && !failed && Array.isArray(keys) && keys.length === 0;

  let body;
  if (failed) {
    body = (
      <GatewayEmptyState
        icon="keys"
        title="API keys could not be loaded"
        description={`The key list did not load${
          readError(keysQuery.error) ? `: ${readError(keysQuery.error)}` : ""
        }. Retry in a moment.`}
        actionLabel="Retry"
        onAction={() => keysQuery.refetch()}
      />
    );
  } else if (noKeys) {
    body = (
      <GatewayEmptyState
        icon="keys"
        title="No API keys yet"
        description="Create a key for each app or service that calls the gateway, so its requests, spend and limits are tracked on their own."
        // Its own name, apart from the header's Create key: a screen reader
        // and a strict-mode walk would otherwise find two identical buttons.
        actionLabel={canCreate ? "Create your first key" : undefined}
        onAction={canCreate ? () => setCreateOpen(true) : undefined}
      />
    );
  } else {
    body = (
      <>
        <GatewayFilterBar
          search={search}
          onSearchChange={setSearch}
          searchPlaceholder="Search name, owner or key prefix"
          actions={
            keys?.length ? (
              <Typography role="status" typography="s2" color="text.secondary">
                {keyCountSummary(keys, rows?.length ?? 0)}
              </Typography>
            ) : null
          }
        >
          <ChipSelector
            options={KEY_STATUS_FILTERS}
            value={statusFilter}
            onChange={setStatusFilter}
            aria-label="Status"
          />
        </GatewayFilterBar>
        <GatewayGrid
          rowData={rows}
          loading={loading}
          columnDefs={columnDefs}
          context={gridContext}
          stateKey="gateway-api-keys"
          getRowId={(p) => p.data.id}
          onRowClicked={(event) => {
            // A click on the row menu is not a click on the row.
            if (fromControl(event)) return;
            setOpenId(event.data.id);
          }}
          rowStyle={{ cursor: "pointer" }}
          tooltipShowDelay={400}
          emptyState={{
            variant: "no-results",
            onAction: () => {
              setSearch("");
              setStatusFilter("");
            },
          }}
        />
      </>
    );
  }

  return (
    <GatewayPageFrame
      title="API keys"
      description="Keys your apps use to call the gateway. A key's secret is shown once, when it is created."
      testId="gateway-keys-page"
      actions={
        <>
          <CreditBalanceChip />
          <GatewayAdminOnly allowed={canWrite}>
            <Button
              variant="outlined"
              startIcon={<FaIcon name="reload" />}
              onClick={handleSync}
              disabled={!canWrite || !gatewayId || syncMutation.isPending}
            >
              {syncMutation.isPending ? "Syncing..." : "Sync from gateway"}
            </Button>
          </GatewayAdminOnly>
          <Button
            variant="contained"
            color="primary"
            startIcon={<FaIcon name="add" />}
            onClick={() => canCreate && setCreateOpen(true)}
            disabled={!canCreate}
          >
            Create key
          </Button>
        </>
      }
    >
      {body}

      <KeyDetailDrawer
        paperRef={detailRef}
        keyId={openId}
        open={Boolean(openId)}
        onClose={() => {
          setOpenId(null);
          setRevokeWarning(null);
        }}
        gatewayId={gatewayId}
        revokeWarning={
          revokeWarning?.keyId === openId ? revokeWarning.message : null
        }
        onEdit={(key) => setEditTarget(key)}
        onRotate={requestRotate}
        onRevoke={requestRevoke}
      />

      <CreateKeyDialog
        open={createShown}
        onClose={closeCreate}
        onCreated={(created) => setReveal({ ...created, notes: [] })}
        gatewayId={gatewayId}
      />

      <EditKeyDialog
        open={Boolean(editTarget)}
        onClose={() => setEditTarget(null)}
        keyData={editTarget}
        gatewayId={gatewayId}
      />

      <ConfirmDialog
        open={Boolean(rotateTarget)}
        onClose={() => setRotateTarget(null)}
        title={rotateTarget ? `Rotate ${rotateTarget.name}?` : "Rotate key"}
        message={ROTATE_DIALOG_MESSAGE}
        confirmLabel="Rotate key"
        confirmColor="primary"
        isLoading={rotateMutation.isPending}
        onConfirm={confirmRotate}
      />

      <ConfirmDialog
        open={Boolean(revokeTarget)}
        onClose={() => setRevokeTarget(null)}
        title={revokeTarget ? `Revoke ${revokeTarget.name}?` : "Revoke key"}
        message="This revokes the key locally and requests revocation at the gateway. If the gateway is unreachable, requests may still be accepted until revocation is synchronized. Local revocation cannot be undone."
        confirmLabel="Revoke key"
        confirmColor="error"
        isLoading={revokeMutation.isPending}
        onConfirm={confirmRevoke}
        onExited={() => {
          // Revocation removes the trigger button. MUI cannot restore focus to
          // that detached node; give keyboard control back to the retained drawer.
          if (openId && revokeWarning?.keyId === openId)
            detailRef.current?.focus();
        }}
      />

      <OneTimeKeyReveal
        reveal={reveal}
        // The confirmed public address, or the marked placeholder: never a
        // loopback, in-cluster or unconfirmed address as the base URL.
        baseUrl={requestEndpoint.url}
        requestBaseUrl={requestEndpoint.url}
        requestBaseUrlNote={
          requestEndpoint.kind === "configured" ? "" : PLACEHOLDER_REQUEST_NOTE
        }
        gatewayId={gatewayId}
        onDone={() => {
          setReveal(null);
          rotateMutation.reset();
        }}
      />
    </GatewayPageFrame>
  );
}

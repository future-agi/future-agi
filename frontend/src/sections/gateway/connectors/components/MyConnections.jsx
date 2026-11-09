import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import ConfirmDialog from "src/sections/gateway/components/ConfirmDialog";

import { GatewayStatusChip } from "../../components/frame";
import { cleanupNotice, disconnectNotice } from "./connection-cleanup";
import { connectTarget } from "../connect-flow";
import { useDisconnectAccount } from "../hooks/useConnectorCatalog";
import {
  useRefreshMyConnections,
  useStartConnect,
} from "../hooks/useMyConnections";

// The reader's own account on the server, on the gateway status chip.
const STATUS = {
  active: { tone: "healthy", label: "Connected" },
  needs_reauth: { tone: "warning", label: "Needs a new sign-in" },
  expired: { tone: "warning", label: "Expired" },
  revoked: { tone: "neutral", label: "Disconnected" },
};
const NOT_CONNECTED = { tone: "neutral", label: "Not connected" };
const NOT_IN_USE = { tone: "neutral", label: "Not in use" };

// Stored readiness is not a vendor-discovery result.
const NOT_STARTABLE = {
  org_oauth_app_required:
    "This app signs in only through your organization's own OAuth app registration with its vendor, such as your Google or Microsoft app. An Owner or Admin registers it under Integrations before anyone can connect.",
  oauth_client_unreadable:
    "Your organization's OAuth app for this server cannot be read. An Owner or Admin replaces it before anyone can connect.",
  client_not_confirmed:
    "Future AGI cannot confirm from here that this server accepts a sign-in without your organization's own OAuth app. An Owner or Admin can add one for it.",
  mcp_off: "MCP is turned off for your organization.",
  per_user_channel_off:
    "This deployment does not connect each person's own account.",
  server_changed: "This server no longer connects each person's own account.",
  sign_in_not_served: "This deployment does not serve this app's sign-in.",
  server_disabled:
    "This MCP server is disabled. You can still disconnect your account.",
};

// A refused start, by the API's code, as what happened and what to do next.
const REFUSED = {
  binding_mismatch:
    "The link you followed was made for someone else. Connect starts your own account instead.",
  binding_invalid:
    "The link you followed is not valid or has expired. Connect starts your own account instead.",
  not_member: "You are no longer a member of this organization.",
  server_not_per_user:
    "This server no longer connects each person's own account.",
  server_not_found: "This server is no longer in your organization.",
  oauth_client_unavailable:
    "This server did not accept the available OAuth client. An Owner or Admin should check the app and the server's supported sign-in methods; an app may already be installed.",
  individual_required: "Sign in with your own individual account to connect.",
  cleanup_pending: "Retry disconnect cleanup before connecting again.",
  connector_oauth_org_client_required:
    "This app signs in only through your organization's own OAuth app registration with its vendor. An Owner or Admin registers it under Integrations before anyone can connect.",
  connector_oauth_client_unreadable:
    "Your organization's OAuth app for this server cannot be read. An Owner or Admin replaces it before anyone can connect.",
  discovery_failed:
    "Future AGI could not read this server's sign-in settings. Try again later.",
  discovery_refused:
    "This server's sign-in address is not allowed on this deployment.",
};
const BINDING_REFUSALS = new Set(["binding_mismatch", "binding_invalid"]);

function refusedText(err) {
  if (REFUSED[err?.code]) return REFUSED[err.code];
  const reason = typeof err?.message === "string" ? err.message.trim() : "";
  if (!reason) return "The sign-in did not start.";
  return `The sign-in did not start: ${reason}${/[.!?]$/.test(reason) ? "" : "."}`;
}

// "Connected" only when the API says the gateway uses the account now.
function statusOf(connection) {
  if (connection.status === "active" && connection.connected !== true) {
    return NOT_IN_USE;
  }
  return STATUS[connection.status] || NOT_CONNECTED;
}

function connectLabel(connection) {
  if (connection.status && connection.status !== "revoked") {
    return "Connect again";
  }
  return "Connect";
}

function connectName(connection, serverId) {
  const again = connectLabel(connection) === "Connect again" ? " again" : "";
  return `Connect your account on ${serverId}${again}`;
}

// Every member manages only their own accounts; Connect is explicit.
export default function MyConnections({
  connections,
  landing,
  onBindingSpent,
  loading = false,
  failed = false,
  retrying = false,
  onRetry,
}) {
  const headingId = useId();
  const [confirming, setConfirming] = useState(null);
  const [refusals, setRefusals] = useState({});
  const [cleanup, setCleanup] = useState({});
  const { start, pending } = useStartConnect();
  const disconnect = useDisconnectAccount();
  const refresh = useRefreshMyConnections();
  const landedId = landing?.serverId || null;
  const landedRow = connections.find((c) => c.server_id === landedId);

  const connect = async (connection) => {
    const binding =
      landedId === connection.server_id ? landing?.binding || null : null;
    setRefusals((current) => ({ ...current, [connection.server_id]: null }));
    try {
      await start(connection, binding);
    } catch (err) {
      if (binding && BINDING_REFUSALS.has(err?.code)) onBindingSpent();
      setRefusals((current) => ({
        ...current,
        [connection.server_id]: refusedText(err),
      }));
    }
  };

  const confirm = async () => {
    const { connection, target } = confirming;
    try {
      const result = await disconnect.mutateAsync({ url: target });
      if (result?.disconnected !== true)
        throw new Error(
          "The disconnect result could not be confirmed. Reload your accounts and try again.",
        );
      const incomplete =
        result?.gateway_synced !== true || result?.cleanup_pending === true;
      setCleanup((current) => ({
        ...current,
        [connection.server_id]: { incomplete, connection },
      }));
      setConfirming(null);
      const notice = disconnectNotice(connection.server_id, result);
      enqueueSnackbar(notice.message, { variant: notice.variant });
    } catch (err) {
      setConfirming(null);
      enqueueSnackbar(
        err?.message ||
          `Your account on ${connection.server_id} could not be disconnected.`,
        { variant: "error" },
      );
    } finally {
      refresh();
    }
  };

  return (
    <Box
      component="section"
      aria-labelledby={headingId}
      aria-busy={loading}
      data-testid="my-connections"
      sx={{ display: "flex", flexDirection: "column", gap: 1, mb: 2 }}
    >
      <Box>
        <Typography
          id={headingId}
          component="h2"
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.primary"
        >
          Your accounts
        </Typography>
        <Typography typography="s2" color="text.secondary">
          Your own accounts on your organization&apos;s MCP servers. Your agents
          call each server with your account once you connect it; no one
          else&apos;s calls use it.
        </Typography>
      </Box>

      {loading && (
        <Typography typography="s2" role="status">
          Loading your accounts...
        </Typography>
      )}
      {failed && (
        <Box>
          <Typography typography="s2" color="error.main" role="alert">
            Your accounts could not be loaded.
            {landedId && " Your connect link is kept for retry."}
            {connections.length > 0 &&
              " The accounts below are from the last successful read."}
          </Typography>
          <Button
            onClick={onRetry}
            disabled={retrying}
            aria-label="Retry your accounts"
          >
            Retry
          </Button>
        </Box>
      )}
      {landedId && !landedRow && !loading && !failed && (
        <Typography typography="s2" color="text.secondary" role="status">
          The link you followed names an MCP server with no account for you to
          connect here.
        </Typography>
      )}

      {connections.length > 0 && (
        <Box
          component="ul"
          sx={{
            listStyle: "none",
            m: 0,
            p: 0,
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          {connections.map((connection, index) => {
            const id = connection.server_id;
            const cleanupPending =
              connection.cleanup_pending === true ||
              (cleanup[id]?.connection === connection &&
                cleanup[id].incomplete);
            const ready =
              connection.start?.state === "ready" &&
              Boolean(connectTarget(connection.start_url));
            const vendorOnly =
              connection.upstream_revocation === "pending" && ready;
            const state =
              cleanupPending && !vendorOnly
                ? { tone: "warning", label: "Cleanup pending" }
                : statusOf(connection);
            const notice = cleanupNotice(
              connection.upstream_revocation,
              cleanupPending,
            );
            const offerConnect =
              ready &&
              connection.connected !== true &&
              (!cleanupPending || vendorOnly);
            const reason = connection.start?.reason;
            // A connected account shows that its server took a sign-in.
            const unproven =
              connection.connected === true &&
              reason === "client_not_confirmed";
            const why = ready || unproven ? null : NOT_STARTABLE[reason];
            const disconnectUrl = connectTarget(connection.disconnect_url);
            const landed = landedId === id;
            return (
              <Box
                component="li"
                key={id}
                data-testid={`my-connection-${id}`}
                aria-current={landed ? "true" : undefined}
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 0.5,
                  px: 1.5,
                  py: 1,
                  borderTop: index ? 1 : 0,
                  borderColor: "divider",
                  ...(landed && {
                    outline: 2,
                    outlineColor: "primary.main",
                    outlineOffset: -2,
                    borderRadius: 1,
                  }),
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    gap: 1,
                    alignItems: "center",
                    justifyContent: "space-between",
                    flexWrap: "wrap",
                  }}
                >
                  <Typography
                    typography="s2_1"
                    color="text.primary"
                    sx={{ overflowWrap: "anywhere", minWidth: 0 }}
                  >
                    {id}
                  </Typography>
                  <Box
                    sx={{
                      display: "flex",
                      gap: 1,
                      alignItems: "center",
                      flexShrink: 0,
                    }}
                  >
                    <GatewayStatusChip
                      tone={state.tone}
                      label={state.label}
                      showIcon={false}
                    />
                    {offerConnect && (
                      <Button
                        size="small"
                        variant="contained"
                        color="primary"
                        aria-label={connectName(connection, id)}
                        disabled={pending === id}
                        onClick={() => connect(connection)}
                      >
                        {connectLabel(connection)}
                      </Button>
                    )}
                    {disconnectUrl && (
                      <Button
                        size="small"
                        variant="outlined"
                        color="error"
                        aria-label={
                          cleanupPending
                            ? `Retry disconnect on ${id}`
                            : `Disconnect your account on ${id}`
                        }
                        onClick={() =>
                          setConfirming({ connection, target: disconnectUrl })
                        }
                      >
                        {cleanupPending ? "Retry disconnect" : "Disconnect"}
                      </Button>
                    )}
                  </Box>
                </Box>
                {landed && offerConnect && (
                  <Typography typography="s2" color="text.secondary">
                    You followed a link to connect your account on this server.
                    It connects when you choose Connect.
                  </Typography>
                )}
                {why && (
                  <Typography typography="s2" color="text.secondary">
                    {why}
                  </Typography>
                )}
                {notice && (
                  <Typography typography="s2" color="warning.main" role="alert">
                    {notice}
                  </Typography>
                )}
                {refusals[id] && (
                  <Typography typography="s2" color="error.main" role="alert">
                    {refusals[id]}
                  </Typography>
                )}
              </Box>
            );
          })}
        </Box>
      )}

      <ConfirmDialog
        open={Boolean(confirming)}
        onClose={() => setConfirming(null)}
        onConfirm={confirm}
        title={
          confirming
            ? `Disconnect your account on ${confirming.connection.server_id}?`
            : ""
        }
        message="Disconnect stops new token delivery and requests upstream revocation and gateway cache eviction. If cleanup cannot be confirmed, retry it here; cached access may remain until eviction or expiry."
        confirmLabel="Disconnect"
        isLoading={disconnect.isPending}
      />
    </Box>
  );
}

MyConnections.propTypes = {
  connections: PropTypes.arrayOf(
    PropTypes.shape({
      server_id: PropTypes.string.isRequired,
      connected: PropTypes.bool,
      cleanup_pending: PropTypes.bool,
      status: PropTypes.string,
      start: PropTypes.shape({
        state: PropTypes.string,
        reason: PropTypes.string,
      }),
      start_url: PropTypes.string,
      disconnect_url: PropTypes.string,
    }),
  ).isRequired,
  landing: PropTypes.shape({
    serverId: PropTypes.string,
    binding: PropTypes.string,
  }),
  onBindingSpent: PropTypes.func.isRequired,
  loading: PropTypes.bool,
  failed: PropTypes.bool,
  retrying: PropTypes.bool,
  onRetry: PropTypes.func,
};

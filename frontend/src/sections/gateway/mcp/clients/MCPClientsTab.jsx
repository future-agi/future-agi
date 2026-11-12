import React, { useState } from "react";
import PropTypes from "prop-types";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Divider,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { useAuthContext } from "src/auth/hooks";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import ConfirmDialog from "../../components/ConfirmDialog";
import { consentError, listConsents, revokeConsent } from "./consentApi";

export default function MCPClientsTab() {
  const { currentOrganizationId } = useOrganization();
  const { user } = useAuthContext();
  const { canEdit } = useGatewayAdmin();
  return (
    <ClientGrants
      key={`${currentOrganizationId}:${user?.id}:${canEdit}`}
      organizationId={currentOrganizationId}
      userId={user?.id}
      canSeeOrganization={canEdit}
    />
  );
}

function ClientGrants({ organizationId, userId, canSeeOrganization }) {
  const [scope, setScope] = useState("self");
  const [selected, setSelected] = useState(null);
  const [revoking, setRevoking] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const client = useQueryClient();
  const queryKey = ["gateway-mcp-consents", organizationId, userId, scope];
  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) => listConsents(scope, organizationId, signal),
    enabled: Boolean(organizationId && userId),
    staleTime: 0,
    retry: false,
  });
  async function revoke() {
    if (revoking || !selected?.can_revoke) return;
    setRevoking(true);
    setError("");
    try {
      const result = await revokeConsent(selected.id, organizationId);
      if (result.revoked !== true) throw new Error("Not revoked");
      setSelected(null);
      setNotice(
        "Access revoked. Refresh is blocked; existing access tokens expire within 15 minutes.",
      );
      await client.invalidateQueries({
        queryKey: ["gateway-mcp-consents", organizationId, userId],
      });
    } catch (err) {
      setError(
        consentError(
          err,
          "Access could not be revoked. Retry or refresh the list.",
        ),
      );
    } finally {
      setRevoking(false);
    }
  }
  return (
    <Stack spacing={2}>
      <Box>
        <Typography component="h2" variant="subtitle1" sx={{ fontWeight: 600 }}>
          MCP clients
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Clients you authorized to use this organization’s gateway. Each
          connection belongs to the person who approved it.
        </Typography>
      </Box>
      {canSeeOrganization && (
        <ToggleButtonGroup
          size="small"
          exclusive
          value={scope}
          onChange={(_, value) => {
            if (value) {
              setScope(value);
              setSelected(null);
              setError("");
            }
          }}
          aria-label="Connections scope"
        >
          <ToggleButton value="self">My connections</ToggleButton>
          <ToggleButton value="organization">Organization clients</ToggleButton>
        </ToggleButtonGroup>
      )}
      {notice && (
        <Alert severity="success" onClose={() => setNotice("")}>
          {notice}
        </Alert>
      )}
      {error && !selected && <Alert severity="error">{error}</Alert>}
      {query.isLoading && (
        <Stack direction="row" spacing={2}>
          <CircularProgress size={20} />
          <Typography role="status" variant="body2">
            Loading MCP clients…
          </Typography>
        </Stack>
      )}
      {query.isError && (
        <Alert
          severity="error"
          action={<Button onClick={() => query.refetch()}>Retry</Button>}
        >
          Could not load MCP clients. Refresh the list to try again.
        </Alert>
      )}
      {!query.isError && query.data?.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          {scope === "self"
            ? "You have no active MCP client connections in this organization. Start sign-in from your MCP client to connect."
            : "No one has authorized an MCP client in this organization."}
        </Typography>
      )}
      {!query.isError &&
        query.data?.map((row) => (
          <Stack
            component="article"
            aria-label={`${row.client_name} connection`}
            spacing={1.5}
            key={row.id}
            sx={{
              p: 2,
              border: 1,
              borderColor: "divider",
              borderRadius: 1,
              overflowWrap: "anywhere",
            }}
          >
            <Stack
              direction={{ xs: "column", sm: "row" }}
              justifyContent="space-between"
              spacing={1}
            >
              <Box>
                <Typography variant="subtitle2">{row.client_name}</Typography>
                <Typography variant="caption" color="text.secondary">
                  Client ID: {row.client_id}
                </Typography>
              </Box>
              {row.can_revoke && (
                <Button
                  color="error"
                  onClick={() => {
                    setError("");
                    setSelected(row);
                  }}
                >
                  Revoke
                </Button>
              )}
            </Stack>
            <Typography variant="body2">
              Approved by {row.user.name} · {row.organization.name}
            </Typography>
            <Typography variant="body2">Resource: {row.resource}</Typography>
            <Typography variant="body2">
              Permissions: {row.scopes.join(", ")}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Approved {new Date(row.granted_at).toLocaleString()}
            </Typography>
          </Stack>
        ))}
      <Divider />
      <Typography variant="caption" color="text.secondary">
        Revoking a connection immediately blocks its refresh tokens. Existing
        access tokens can remain valid for up to 15 minutes. Other people’s
        connections are read-only.
      </Typography>
      <ConfirmDialog
        open={Boolean(selected)}
        onClose={() => {
          if (!revoking) setSelected(null);
        }}
        onConfirm={revoke}
        title={`Revoke ${selected?.client_name || "client"}?`}
        message={
          error ||
          "This stops the client from refreshing access to your account in this organization. Existing access tokens expire within 15 minutes. To reconnect, sign in and approve again from the client."
        }
        confirmLabel="Revoke access"
        isLoading={revoking}
      />
    </Stack>
  );
}
ClientGrants.propTypes = {
  organizationId: PropTypes.string,
  userId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  canSeeOrganization: PropTypes.bool.isRequired,
};

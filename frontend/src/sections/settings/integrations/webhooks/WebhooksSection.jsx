import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Card,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  WebhooksScope,
  useDestinations,
  useWebhookEndpoints,
  useWebhookPolicy,
} from "src/api/webhooks";
import AddWebhookDialog from "./AddWebhookDialog";
import HttpUrlWarning from "./HttpUrlWarning";
import SigningSecretDialog from "./SigningSecretDialog";
import WebhookPolicyCard from "./WebhookPolicyCard";
import {
  EMPTY_STATE,
  HTTP_EMPTY_STATE,
  ORG_WIDE_ROW,
  fmtDateTime,
  statusLine,
} from "./copy";
import { endpointPath } from "./paths";

const TONE = {
  success: "success",
  warning: "warning",
  error: "error",
  default: "default",
};

function delivered(stats) {
  if (!stats || !stats.total) return "–";
  const pct = Math.round((stats.succeeded / stats.total) * 100);
  return `${stats.succeeded.toLocaleString()} of ${stats.total.toLocaleString()} (${pct}%)`;
}

function eventsText(subs) {
  return (subs || []).map((s) => s.event_type).join(", ");
}

// Settings > Integrations > Webhooks (DESIGN.md section 11.2): the list, the
// empty state, Add webhook, the secret shown once, and the policy card.
function WebhooksList({ access, destinations, workspaceRouteId }) {
  const navigate = useNavigate();
  const orgPage = !workspaceRouteId;
  const ws = access.workspace_id;
  const {
    data: rows = [],
    isLoading,
    isError,
  } = useWebhookEndpoints(ws ? { workspace: ws } : {});
  const { data: orgPolicy } = useWebhookPolicy(null);
  const [adding, setAdding] = useState(false);
  const [created, setCreated] = useState(null);

  const byId = useMemo(
    () =>
      Object.fromEntries(
        (destinations || [])
          .filter((d) => d.kind === "webhook")
          .map((d) => [d.id, d]),
      ),
    [destinations],
  );

  const open = (id) => navigate(endpointPath(id, workspaceRouteId));

  return (
    <Box component="section" aria-labelledby="webhooks-heading" sx={{ mt: 4 }}>
      <Box
        display="flex"
        justifyContent="space-between"
        alignItems="center"
        mb={2}
      >
        <Box>
          <Typography
            id="webhooks-heading"
            component="h2"
            sx={{ typography: "m3", fontWeight: "fontWeightSemiBold" }}
          >
            Webhooks
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            Signed HTTP requests to your own endpoint when prompts and
            deployments change.
          </Typography>
        </Box>
        {rows.length > 0 && (
          <Button
            variant="contained"
            size="small"
            startIcon={<Iconify icon="solar:add-circle-bold" />}
            onClick={() => setAdding(true)}
            disabled={!access.can_manage && !access.org_admin}
          >
            Add webhook
          </Button>
        )}
      </Box>

      {isError && (
        <Alert severity="error">
          Failed to load webhooks. Please try again later.
        </Alert>
      )}

      {!isLoading && !isError && rows.length === 0 && (
        <Card
          variant="outlined"
          sx={{ p: 4, textAlign: "center", borderStyle: "dashed" }}
        >
          <Typography sx={{ typography: "s1", color: "text.secondary", mb: 2 }}>
            {orgPolicy?.url_policy?.http_allowed === true
              ? HTTP_EMPTY_STATE
              : EMPTY_STATE}
          </Typography>
          <Button
            variant="contained"
            size="small"
            startIcon={<Iconify icon="solar:add-circle-bold" />}
            onClick={() => setAdding(true)}
            disabled={!access.can_manage && !access.org_admin}
          >
            Add webhook
          </Button>
        </Card>
      )}

      {rows.length > 0 && (
        <TableContainer component={Card} variant="outlined">
          <Table size="small" aria-label="Webhooks">
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Sends to</TableCell>
                <TableCell>Events</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Delivered (24 h)</TableCell>
                <TableCell>Last delivery</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((ep) => {
                const reg = byId[ep.id];
                const st = statusLine(ep);
                return (
                  <TableRow
                    key={ep.id}
                    hover
                    sx={{ cursor: "pointer" }}
                    onClick={() => open(ep.id)}
                  >
                    <TableCell>
                      <Typography variant="body2" fontWeight={500}>
                        {ep.name}
                      </Typography>
                      {ep.org_wide && !orgPage && (
                        <Typography variant="caption" color="text.secondary">
                          {ORG_WIDE_ROW}
                        </Typography>
                      )}
                      {ep.org_wide && orgPage && (
                        <Typography variant="caption" color="text.secondary">
                          Org-wide
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      {ep.url_host}
                      <HttpUrlWarning
                        urlScheme={ep.url_scheme}
                        badgeLabel="Unencrypted (HTTP)"
                      />
                    </TableCell>
                    <TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>
                      {eventsText(ep.subscriptions)}
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        variant="soft"
                        color={TONE[st.tone]}
                        label={st.text}
                      />
                    </TableCell>
                    <TableCell>{delivered(reg?.stats_24h)}</TableCell>
                    <TableCell>{fmtDateTime(reg?.last_used_at)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <WebhookPolicyCard access={access} workspaceRouteId={workspaceRouteId} />

      <AddWebhookDialog
        open={adding}
        onClose={() => setAdding(false)}
        access={access}
        orgPage={orgPage}
        fullPayloadAllowed={!!orgPolicy?.full_payload_allowed}
        urlPolicy={orgPolicy?.url_policy}
        onCreated={(res) => {
          setAdding(false);
          setCreated(res);
        }}
      />
      <SigningSecretDialog
        open={!!created}
        secret={created?.secret}
        endpoint={created?.endpoint}
        title={created ? `${created.endpoint.name} is ready` : ""}
        onClose={() => setCreated(null)}
      />
    </Box>
  );
}

WebhooksList.propTypes = {
  access: PropTypes.object.isRequired,
  destinations: PropTypes.array,
  workspaceRouteId: PropTypes.string,
};

// The one destination registry (DESIGN.md section 11.1) decides whether the
// section exists: only where this install can send and the caller may see it;
// otherwise nothing at all, never "coming soon".
function WebhooksGate({ workspaceRouteId }) {
  const { data: registry } = useDestinations();
  const webhooksAccess = registry?.webhooks;
  const showWebhooks = !!(
    webhooksAccess?.available && webhooksAccess?.can_view
  );
  if (!showWebhooks) return null;
  return (
    <WebhooksList
      access={webhooksAccess}
      destinations={registry.destinations}
      workspaceRouteId={workspaceRouteId}
    />
  );
}

WebhooksGate.propTypes = { workspaceRouteId: PropTypes.string };

// On a workspace's settings page every webhooks request names that workspace
// (WebhooksScope), so the list, Add webhook, the access flags and the policy
// card are that workspace's, whatever the session's current workspace is.
export default function WebhooksSection({ workspaceRouteId }) {
  return (
    <WebhooksScope value={workspaceRouteId || null}>
      <WebhooksGate workspaceRouteId={workspaceRouteId} />
    </WebhooksScope>
  );
}

WebhooksSection.propTypes = { workspaceRouteId: PropTypes.string };

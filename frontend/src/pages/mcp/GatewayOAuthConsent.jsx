import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useSearchParams } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Divider,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { Helmet } from "react-helmet-async";
import { useAuthContext } from "src/auth/hooks";
import {
  consentError,
  decideConsent,
  loadConsent,
  returnToClient,
} from "src/sections/gateway/mcp/clients/consentApi";

export default function GatewayOAuthConsent() {
  const [params] = useSearchParams();
  const requests = params.getAll("request");
  const requestId =
    requests.length === 1 && /^[A-Za-z0-9_-]{20,128}$/.test(requests[0])
      ? requests[0]
      : "";
  return <ConsentRequest key={requestId} requestId={requestId} />;
}

function ConsentRequest({ requestId }) {
  const { user } = useAuthContext();
  const [detail, setDetail] = useState(null);
  const [organizationId, setOrganizationId] = useState("");
  const [loading, setLoading] = useState(Boolean(requestId));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(
    requestId
      ? ""
      : "This sign-in link is incomplete. Start again from your MCP client.",
  );
  const busy = useRef(false);
  const active = useRef(false);

  useEffect(() => {
    active.current = true;
    const controller = new AbortController();
    if (requestId) {
      loadConsent(requestId, controller.signal)
        .then((data) => {
          if (controller.signal.aborted) return;
          setDetail(data);
          setOrganizationId(
            data.organizations.length === 1 ? data.organizations[0].id : "",
          );
        })
        .catch((err) => {
          if (!controller.signal.aborted)
            setError(
              consentError(
                err,
                "Could not load this sign-in request. Start again from your MCP client.",
              ),
            );
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }
    return () => {
      active.current = false;
      controller.abort();
    };
  }, [requestId]);

  async function decide(decision) {
    if (busy.current) return;
    busy.current = true;
    setSubmitting(true);
    setError("");
    try {
      const answer = await decideConsent(requestId, decision, organizationId);
      if (active.current)
        returnToClient(answer.redirect_to, detail.redirect_uri);
    } catch (err) {
      if (active.current) {
        setError(
          consentError(
            err,
            "The decision could not be completed. Start again from your MCP client if this request has expired.",
          ),
        );
        setSubmitting(false);
        busy.current = false;
      }
    }
  }

  return (
    <Box
      component="main"
      sx={{
        minHeight: "100vh",
        bgcolor: "background.default",
        px: 2,
        py: { xs: 3, sm: 8 },
      }}
    >
      <Helmet>
        <title>Connect an MCP client</title>
        <meta name="referrer" content="no-referrer" />
      </Helmet>
      <Stack
        spacing={3}
        sx={{
          maxWidth: 560,
          mx: "auto",
          p: { xs: 2, sm: 3 },
          bgcolor: "background.paper",
          border: 1,
          borderColor: "divider",
          borderRadius: 1,
          boxShadow: "none",
        }}
      >
        <Typography component="h1" variant="h6" sx={{ fontWeight: 600 }}>
          Connect an MCP client
        </Typography>
        {loading && (
          <Stack direction="row" spacing={2} alignItems="center">
            <CircularProgress size={20} />
            <Typography role="status" variant="body2">
              Loading sign-in request…
            </Typography>
          </Stack>
        )}
        {error && <Alert severity="error">{error}</Alert>}
        {!loading && detail && (
          <>
            <Box sx={{ overflowWrap: "anywhere" }}>
              <Typography variant="subtitle2">{detail.client.name}</Typography>
              <Typography variant="body2" color="text.secondary">
                wants to access the gateway on your behalf.
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Client ID: {detail.client.client_id}
              </Typography>
            </Box>
            {!detail.client.verified && (
              <Alert severity="warning">
                This client is not verified. Approve only if you recognize it
                and started this sign-in.
              </Alert>
            )}
            <Typography variant="body2">
              Signed in as {user?.email || user?.name || "you"}. This grants
              access for your account only.
            </Typography>
            <TextField
              select
              fullWidth
              size="small"
              label="Organization"
              value={organizationId}
              onChange={(event) => setOrganizationId(event.target.value)}
              disabled={submitting}
            >
              {detail.organizations.map((org) => (
                <MenuItem key={org.id} value={org.id}>
                  {org.name}
                </MenuItem>
              ))}
            </TextField>
            <Stack spacing={1} sx={{ overflowWrap: "anywhere" }}>
              <Typography variant="subtitle2">Requested access</Typography>
              <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                {detail.scopes.map((scope) => (
                  <Box component="li" key={scope.name} sx={{ mb: 1 }}>
                    <Typography variant="body2">{scope.description}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {scope.name}
                    </Typography>
                  </Box>
                ))}
              </Box>
              <Typography variant="body2">
                Resource: {detail.resource}
              </Typography>
              <Typography variant="body2">
                Return to: {detail.redirect_uri}
              </Typography>
            </Stack>
            <Divider />
            <Typography variant="body2" color="text.secondary">
              Revoke this grant in Gateway → MCP tools → MCP clients. Revocation
              stops refresh immediately; existing access tokens may work for up
              to 15 minutes.
            </Typography>
            <Stack direction="row" spacing={2} justifyContent="flex-end">
              <Button
                variant="outlined"
                color="inherit"
                disabled={submitting}
                onClick={() => decide("deny")}
              >
                Deny
              </Button>
              <Button
                variant="contained"
                disabled={submitting || !organizationId}
                onClick={() => decide("approve")}
              >
                {submitting ? "Returning to client…" : "Approve"}
              </Button>
            </Stack>
          </>
        )}
      </Stack>
    </Box>
  );
}
ConsentRequest.propTypes = { requestId: PropTypes.string.isRequired };

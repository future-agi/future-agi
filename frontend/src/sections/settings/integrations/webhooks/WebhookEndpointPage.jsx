import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Card,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Link,
  Menu,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { LoadingScreen } from "src/components/loading-screen";
import {
  WebhooksScope,
  useDeleteWebhook,
  useDestinations,
  useResumeWebhook,
  useRevokePreviousSecret,
  useRotateSecret,
  useSendTest,
  useSetWebhookEnabled,
  useUpdateWebhook,
  useWebhookEndpoint,
  useWebhookPolicy,
} from "src/api/webhooks";
import ActivityCard from "./ActivityCard";
import DeliveriesCard from "./DeliveriesCard";
import SigningSecretDialog from "./SigningSecretDialog";
import TestResult from "./TestResult";
import WebhookFields from "./WebhookFields";
import { OVERLAP_CHOICES, fmtDateTime, lagLine, statusLine } from "./copy";
import {
  apiError,
  errorStatus,
  formOf,
  headersRetypeMessage,
  headersWithoutValue,
  updateBody,
} from "./form";
import { integrationsPath } from "./paths";

const TONE = {
  success: "success",
  warning: "warning",
  error: "error",
  default: "default",
};

// `rotating` is the page's, so the ⋯ menu's "Rotate secret" opens the same
// dialog as the card's Rotate… button.
function SecretCard({ endpoint, rotating, setRotating }) {
  const [overlap, setOverlap] = useState(86400);
  const [secret, setSecret] = useState(null);
  const rotate = useRotateSecret(endpoint.id);
  const revoke = useRevokePreviousSecret(endpoint.id);
  const active = (endpoint.secrets || []).find((s) => s.state === "active");
  const previous = (endpoint.secrets || []).find((s) => s.state === "previous");

  return (
    <Card
      variant="outlined"
      sx={{ p: 2.5 }}
      component="section"
      aria-label="Signing secret"
    >
      <Typography
        sx={{ typography: "s1", fontWeight: "fontWeightSemiBold", mb: 1 }}
      >
        Signing secret
      </Typography>
      {endpoint.secrets === null && (
        <Typography variant="body2" color="text.secondary">
          Only admins can see the signing secrets.
        </Typography>
      )}
      {active && (
        <Typography variant="body2">
          Active: …{active.tail}, created {fmtDateTime(active.created_at)}
        </Typography>
      )}
      {previous && (
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography variant="body2">
            Previous: …{previous.tail}, works until{" "}
            {fmtDateTime(previous.expires_at)}
          </Typography>
          {endpoint.can_manage && (
            <Button
              size="small"
              color="error"
              onClick={() => revoke.mutate()}
              disabled={revoke.isPending}
            >
              Stop it now
            </Button>
          )}
        </Stack>
      )}
      {(rotate.isError || revoke.isError) && (
        <Alert severity="error" sx={{ mt: 1 }}>
          {
            apiError(
              rotate.error || revoke.error,
              "Could not change the secret.",
            ).message
          }
        </Alert>
      )}
      {/* Whoever may manage the webhook may rotate (section 9.1), also a Member
          who cannot see the secrets' tails. */}
      {endpoint.can_manage && (
        <Button
          size="small"
          variant="outlined"
          sx={{ mt: 1.5 }}
          onClick={() => setRotating(true)}
        >
          Rotate…
        </Button>
      )}
      <Dialog open={rotating} onClose={() => setRotating(false)}>
        <DialogTitle>Rotate the signing secret</DialogTitle>
        <DialogContent>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            flexWrap="wrap"
          >
            <Typography variant="body2">
              Keep the current secret working for
            </Typography>
            <TextField
              select
              size="small"
              value={overlap}
              onChange={(e) => setOverlap(Number(e.target.value))}
              inputProps={{ "aria-label": "Overlap" }}
            >
              {OVERLAP_CHOICES.map((c) => (
                <MenuItem key={c.seconds} value={c.seconds}>
                  {c.label}
                </MenuItem>
              ))}
            </TextField>
            <Typography variant="body2">
              so you can update your receiver.
            </Typography>
          </Stack>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            Both secrets sign every request until then.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRotating(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={rotate.isPending}
            onClick={() =>
              rotate.mutate(
                { overlapSeconds: overlap },
                {
                  onSuccess: (res) => {
                    setRotating(false);
                    setSecret(res.data.result.secret);
                    // Held only by the secret dialog from here (review S8).
                    rotate.reset();
                  },
                },
              )
            }
          >
            Rotate
          </Button>
        </DialogActions>
      </Dialog>
      <SigningSecretDialog
        open={!!secret}
        secret={secret}
        title="New signing secret"
        onClose={() => setSecret(null)}
      />
    </Card>
  );
}

SecretCard.propTypes = {
  endpoint: PropTypes.object.isRequired,
  rotating: PropTypes.bool,
  setRotating: PropTypes.func.isRequired,
};

function SettingsCard({ endpoint, access, fullPayloadAllowed, urlPolicy }) {
  const [form, setForm] = useState(() => formOf(endpoint));
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);
  const update = useUpdateWebhook(endpoint.id);
  // Reset only when the saved endpoint changes, not on every health refresh.
  useEffect(() => {
    setForm(formOf(endpoint));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpoint.id, endpoint.updated_at]);

  const save = () => {
    setError(null);
    setSaved(false);
    const blank = headersWithoutValue(form);
    if (blank.length) {
      setError({
        code: null,
        attr: "headers",
        message: headersRetypeMessage(blank),
      });
      return;
    }
    const body = updateBody(form, endpoint);
    if (Object.keys(body).length === 0) {
      setSaved("nothing");
      return;
    }
    update.mutate(body, {
      onSuccess: (res) => {
        setForm(formOf(res.data.result));
        setSaved(true);
      },
      onError: (err) => setError(apiError(err, "Could not save the webhook.")),
    });
  };

  return (
    <Card
      variant="outlined"
      sx={{ p: 2.5 }}
      component="section"
      aria-label="Settings"
    >
      <Typography
        sx={{ typography: "s1", fontWeight: "fontWeightSemiBold", mb: 2 }}
      >
        Settings
      </Typography>
      <Box sx={{ maxWidth: 640 }}>
        {error && !["name", "url"].includes(error.attr) && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error.message}
          </Alert>
        )}
        <fieldset
          disabled={!endpoint.can_manage}
          style={{ border: 0, padding: 0, margin: 0 }}
        >
          <WebhookFields
            form={form}
            setForm={setForm}
            access={access}
            fullPayloadAllowed={fullPayloadAllowed}
            urlPolicy={urlPolicy}
            fieldError={error}
          />
        </fieldset>
        {saved === true && (
          <Alert severity="success" sx={{ mt: 2 }} role="status">
            Saved.
          </Alert>
        )}
        {saved === "nothing" && (
          <Alert severity="info" sx={{ mt: 2 }} role="status">
            Nothing changed.
          </Alert>
        )}
        {endpoint.can_manage && (
          <Button
            variant="contained"
            sx={{ mt: 2 }}
            onClick={save}
            disabled={update.isPending}
          >
            Save changes
          </Button>
        )}
      </Box>
    </Card>
  );
}

SettingsCard.propTypes = {
  endpoint: PropTypes.object.isRequired,
  access: PropTypes.object,
  fullPayloadAllowed: PropTypes.bool,
  urlPolicy: PropTypes.object,
};

// The endpoint page's content, inside the endpoint's own workspace scope: what
// the caller may do here (bulk redelivery, resume, the full payload, run
// targeting) is decided where the endpoint lives, not by the session's or the
// page's workspace. Until that registry answer arrives, the page's is used.
function EndpointView({ ep, pageAccess, back }) {
  const navigate = useNavigate();
  const endpointId = ep.id;
  const { data: scoped } = useDestinations();
  const access = scoped?.webhooks || pageAccess;
  const { data: orgPolicy } = useWebhookPolicy(null);
  const toggle = useSetWebhookEnabled(endpointId);
  const resume = useResumeWebhook(endpointId);
  const del = useDeleteWebhook(endpointId);
  const test = useSendTest();
  const [testMenu, setTestMenu] = useState(null);
  const [moreMenu, setMoreMenu] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [rotating, setRotating] = useState(false);

  const st = statusLine(ep);
  const lag = lagLine(ep.backlog);
  const enabled = ep.status === "enabled";
  const types = ep.subscriptions
    .map((s) => s.event_type)
    .filter((t) => !t.endsWith("*"));
  const paused = ep.backlog?.paused || 0;
  const firstError = toggle.error || resume.error || del.error || test.error;

  return (
    <>
      <Helmet>
        <title>{`${ep.name} | Webhooks`}</title>
      </Helmet>
      <Stack spacing={2.5}>
        <Link
          component={RouterLink}
          to={back}
          underline="hover"
          variant="body2"
        >
          ← Integrations
        </Link>
        <Stack
          direction="row"
          alignItems="flex-start"
          justifyContent="space-between"
          spacing={2}
        >
          <Box>
            <Typography variant="h5" component="h1">
              {ep.name}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {ep.url_host}
              {ep.org_wide ? " · Org-wide" : ""}
            </Typography>
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              sx={{ mt: 1 }}
            >
              {/* A labelled group, so the chip keeps its own text as its name. */}
              <Box component="span" role="group" aria-label="Webhook status">
                <Chip
                  size="small"
                  variant="soft"
                  color={TONE[st.tone]}
                  label={st.text}
                />
              </Box>
              {lag && (
                <Typography variant="body2" color="text.secondary">
                  {lag}
                </Typography>
              )}
            </Stack>
          </Box>
          {ep.can_manage && (
            <Stack direction="row" spacing={1}>
              <Button
                variant="outlined"
                onClick={() => toggle.mutate({ enabled: !enabled })}
                disabled={toggle.isPending}
              >
                {enabled ? "Turn off" : "Turn on"}
              </Button>
              <Button
                variant="contained"
                endIcon={<Iconify icon="eva:arrow-ios-downward-fill" />}
                onClick={(e) => setTestMenu(e.currentTarget)}
                disabled={test.isPending || types.length === 0}
              >
                {test.isPending ? "Sending test…" : "Send test"}
              </Button>
              <IconButton
                aria-label="More actions"
                onClick={(e) => setMoreMenu(e.currentTarget)}
              >
                <Iconify icon="eva:more-horizontal-fill" />
              </IconButton>
            </Stack>
          )}
        </Stack>

        <Menu
          anchorEl={testMenu}
          open={!!testMenu}
          onClose={() => setTestMenu(null)}
        >
          {types.map((t) => (
            <MenuItem
              key={t}
              onClick={() => {
                setTestMenu(null);
                test.mutate({ id: ep.id, eventType: t });
              }}
            >
              {t}
            </MenuItem>
          ))}
        </Menu>
        <Menu
          anchorEl={moreMenu}
          open={!!moreMenu}
          onClose={() => setMoreMenu(null)}
        >
          <MenuItem
            onClick={() => {
              setMoreMenu(null);
              setRotating(true);
            }}
          >
            Rotate secret
          </MenuItem>
          <MenuItem
            sx={{ color: "error.main" }}
            onClick={() => {
              setMoreMenu(null);
              setDeleting(true);
            }}
          >
            Delete
          </MenuItem>
        </Menu>

        {firstError && (
          <Alert severity="error">{apiError(firstError).message}</Alert>
        )}
        {test.data && (
          <TestResult
            result={test.data.data.result}
            status={test.data.status}
          />
        )}

        {paused > 0 &&
          enabled &&
          (access.can_admin || (ep.org_wide && access.org_admin)) && (
            <Alert
              severity="warning"
              action={
                <Stack direction="row" spacing={1}>
                  <Button
                    size="small"
                    onClick={() => resume.mutate({ mode: "send" })}
                    disabled={resume.isPending}
                  >
                    Send them
                  </Button>
                  <Button
                    size="small"
                    onClick={() => resume.mutate({ mode: "skip" })}
                    disabled={resume.isPending}
                  >
                    Skip them
                  </Button>
                </Stack>
              }
            >
              {paused.toLocaleString()}{" "}
              {paused === 1 ? "delivery was" : "deliveries were"} held while
              this webhook was off.
            </Alert>
          )}
        {resume.isSuccess && (
          <Alert severity="success" role="status">
            {resume.data.data.result.mode === "send"
              ? `${resume.data.data.result.sent} deliveries sent again.`
              : `${resume.data.data.result.skipped} deliveries skipped.`}
          </Alert>
        )}

        <DeliveriesCard endpoint={ep} access={access} />
        <SettingsCard
          endpoint={ep}
          access={access}
          fullPayloadAllowed={!!orgPolicy?.full_payload_allowed}
          urlPolicy={orgPolicy?.url_policy}
        />
        <SecretCard
          endpoint={ep}
          rotating={rotating}
          setRotating={setRotating}
        />
        <ActivityCard endpoint={ep} />
      </Stack>

      <Dialog open={deleting} onClose={() => setDeleting(false)}>
        <DialogTitle>Delete {ep.name}?</DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            Its waiting deliveries are cancelled and its signing secrets are
            destroyed. The delivery log is kept.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleting(false)}>Cancel</Button>
          <Button
            color="error"
            variant="contained"
            disabled={del.isPending}
            onClick={() =>
              del.mutate(undefined, {
                onSuccess: () => navigate(back, { replace: true }),
              })
            }
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

EndpointView.propTypes = {
  ep: PropTypes.object.isRequired,
  pageAccess: PropTypes.object.isRequired,
  back: PropTypes.string.isRequired,
};

// The endpoint page (DESIGN.md section 11.2): header with the status and lag
// lines, Turn off/on, Send test, Rotate and Delete; the delivery log; the
// settings; the signing secret. Absent (a redirect) where webhooks don't work.
function EndpointPage({ endpointId, workspaceRouteId }) {
  const navigate = useNavigate();
  const back = integrationsPath(workspaceRouteId);
  const { data: registry, isLoading: regLoading } = useDestinations();
  const access = registry?.webhooks;
  const available = !!access?.available;
  const {
    data: ep,
    isLoading,
    isError,
    error,
  } = useWebhookEndpoint(endpointId, {
    enabled: available,
    refetchInterval: 5000,
  });

  useEffect(() => {
    if (!regLoading && registry && !available)
      navigate(back, { replace: true });
  }, [regLoading, registry, available, navigate, back]);

  if (regLoading || (available && isLoading))
    return <LoadingScreen sx={{ minHeight: "60vh" }} />;
  if (!available) return null;
  if (isError) {
    return (
      <Box py={4}>
        <Alert severity="error">
          {errorStatus(error) === 404
            ? "This webhook doesn't exist or was deleted."
            : "This webhook could not be loaded."}
        </Alert>
        <Link component={RouterLink} to={back}>
          Back to Integrations
        </Link>
      </Box>
    );
  }
  return (
    <WebhooksScope value={ep.workspace_id || workspaceRouteId || null}>
      <EndpointView ep={ep} pageAccess={access} back={back} />
    </WebhooksScope>
  );
}

EndpointPage.propTypes = {
  endpointId: PropTypes.string,
  workspaceRouteId: PropTypes.string,
};

export default function WebhookEndpointPage() {
  const { endpointId, workspaceId } = useParams();
  return (
    <WebhooksScope value={workspaceId || null}>
      <EndpointPage endpointId={endpointId} workspaceRouteId={workspaceId} />
    </WebhooksScope>
  );
}

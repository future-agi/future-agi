import React, { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  Chip,
  Grid,
  Typography,
  useTheme,
} from "@mui/material";
import ErrorState from "src/components/error-state";
import PageHeader from "src/components/page-header";
import Section from "src/components/section";
import Iconify from "src/components/iconify";
import { LoadingScreen } from "src/components/loading-screen";
import {
  integrationKeys,
  useIntegrationConnections,
  useStartSlackInstall,
} from "src/api/integrations";
import RunStatusBadge from "src/components/status-badge/StatusBadge";
import IntegrationCard from "./IntegrationCard";
import PlatformLogo from "./PlatformLogo";
import AddIntegrationWizard from "./AddIntegrationWizard";
import useOfferedPlatforms from "./useOfferedPlatforms";
import { useCanManageIntegrations } from "./access";
import { useIntegrationsWorkspaceId } from "./scope";
import IntegrationAdminOnly from "./IntegrationAdminOnly";
import { getErrorMessage } from "./utils";
import WebhooksSection from "./webhooks/WebhooksSection";

const SLACK_RETURN_TO_KEY = "slack-integration-return-to";
const SLACK_CONNECTED = "Slack workspace connected successfully.";
// The workspace page a Slack authorization started from: Slack returns to
// Settings > Integrations, which sends the browser on to that page.
const SLACK_WORKSPACE_PAGE_KEY = "slack-integration-workspace-page";
const WORKSPACE_PAGE =
  /^\/dashboard\/settings\/workspace\/[^/?#]+\/integrations$/;
// A connection that is working: a platform's count badge is a success only
// while every one of its connections is in one of these.
const WORKING_STATUSES = new Set(["active", "syncing", "backfilling"]);

export default function IntegrationsList() {
  const canManage = useCanManageIntegrations();
  const theme = useTheme();
  const platforms = useOfferedPlatforms();
  const offersIssues = platforms.some((p) => p.id === "linear");
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const workspaceId = useIntegrationsWorkspaceId();
  // A failed read is shown on the page, once (no toast repeats it).
  const { data, isLoading, isError, isRefetching, refetch } =
    useIntegrationConnections({
      meta: { errorHandled: true },
    });
  // The Webhooks section remains scoped to the workspace in this route.
  const { workspaceId: workspaceRouteId } = useParams();
  const { mutate: startSlackInstall, isPending: connectingSlack } =
    useStartSlackInstall();
  const autoStartedSlack = useRef(false);
  const [slackError, setSlackError] = useState("");
  // { text, severity }: the Slack connect outcome, or why it cannot start.
  const [slackNotice, setSlackNotice] = useState(null);
  // null = wizard closed, "" = wizard open (no platform), "langfuse" = wizard open with platform
  const [selectedPlatform, setSelectedPlatform] = useState(null);

  const wizardOpen = selectedPlatform !== null;
  const connections = Array.isArray(data) ? data : [];
  const slackConnected = connections.some((c) => c.platform === "slack");

  // "Connected" holds only while Slack is: a Disconnect clears it.
  useEffect(() => {
    if (
      !isLoading &&
      !slackConnected &&
      slackNotice?.text === SLACK_CONNECTED
    ) {
      setSlackNotice(null);
    }
  }, [isLoading, slackConnected, slackNotice]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const slack = params.get("slack");
    const workspacePage = slack
      ? sessionStorage.getItem(SLACK_WORKSPACE_PAGE_KEY)
      : null;
    if (slack) sessionStorage.removeItem(SLACK_WORKSPACE_PAGE_KEY);
    if (
      (slack === "connected" || slack === "error") &&
      WORKSPACE_PAGE.test(workspacePage || "") &&
      workspacePage !== location.pathname
    ) {
      navigate(`${workspacePage}?slack=${slack}`, { replace: true });
      return;
    }
    if (slack === "connected") {
      queryClient.invalidateQueries({
        queryKey: integrationKeys.connections(),
      });
      const returnTo = sessionStorage.getItem(SLACK_RETURN_TO_KEY);
      sessionStorage.removeItem(SLACK_RETURN_TO_KEY);
      if (returnTo?.startsWith("/") && !returnTo.startsWith("//")) {
        navigate(
          `${returnTo}${returnTo.includes("?") ? "&" : "?"}resume_alert=1`,
          { replace: true },
        );
      } else {
        setSlackNotice({
          text: SLACK_CONNECTED,
          severity: "success",
        });
      }
    } else if (params.get("slack") === "error") {
      setSlackError("Could not connect Slack. Please try again.");
    }
  }, [location.pathname, location.search, navigate, queryClient]);

  const connectSlack = useCallback(() => {
    setSlackError("");
    const params = new URLSearchParams(location.search);
    const returnTo = params.get("return_to");
    if (returnTo?.startsWith("/") && !returnTo.startsWith("//")) {
      sessionStorage.setItem(SLACK_RETURN_TO_KEY, returnTo);
    }
    if (workspaceId) {
      sessionStorage.setItem(SLACK_WORKSPACE_PAGE_KEY, location.pathname);
    }
    startSlackInstall(undefined, {
      onSuccess: (response) => {
        const authorizationUrl = response.data?.result?.authorization_url;
        if (!authorizationUrl) {
          setSlackError(
            "Could not start Slack authorization. Please try again.",
          );
          return;
        }
        window.location.assign(authorizationUrl);
      },
      onError: (error) =>
        setSlackError(getErrorMessage(error, "Could not connect Slack.")),
    });
  }, [location.pathname, location.search, startSlackInstall, workspaceId]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (
      params.get("platform") === "slack" &&
      !params.has("slack") &&
      !isLoading &&
      !autoStartedSlack.current
    ) {
      autoStartedSlack.current = true;
      if (canManage) connectSlack();
      else
        setSlackNotice({
          text: "Only an Owner or Admin can connect Slack. Ask one to connect it here.",
          severity: "warning",
        });
    }
  }, [canManage, connectSlack, isLoading, location.search]);

  const selectPlatform = (platformId) => {
    if (!canManage || !platforms.some((p) => p.id === platformId)) return;
    if (platformId === "slack") connectSlack();
    else setSelectedPlatform(platformId);
  };

  if (isLoading) {
    return <LoadingScreen sx={{ height: "100%", minHeight: "60vh" }} />;
  }

  return (
    <Box>
      {slackError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {slackError}
        </Alert>
      )}
      {slackNotice && (
        <Alert severity={slackNotice.severity} sx={{ mb: 2 }}>
          {slackNotice.text}
        </Alert>
      )}
      {connectingSlack && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Starting Slack authorization…
        </Alert>
      )}
      {/* Header */}
      <PageHeader
        title="Integrations"
        description="Connect external platforms"
        sx={{ mb: 3 }}
        actions={
          <IntegrationAdminOnly allowed={canManage}>
            <Button
              variant="contained"
              color="primary"
              size="small"
              startIcon={<Iconify icon="solar:add-circle-bold" />}
              onClick={() => setSelectedPlatform("")}
              disabled={!canManage}
            >
              Add Integration
            </Button>
          </IntegrationAdminOnly>
        }
      />

      {/* Active Connections: a failed read is shown here, with a Retry, and
          the rest of the page (Add Integration, platforms, Webhooks) stays. */}
      {isError ? (
        <Section variant="panel" sx={{ mb: theme.spacing(3) }}>
          <ErrorState
            role="alert"
            title="Failed to load integrations"
            detail="Check your connection and retry."
            onRetry={() => refetch()}
            retrying={isRefetching}
            sx={{ height: "auto", flexGrow: 0, py: 4 }}
          />
        </Section>
      ) : connections.length > 0 ? (
        <Box mb={theme.spacing(4)}>
          <Typography
            sx={{
              typography: "s2",
              fontWeight: "fontWeightMedium",
              color: "text.tertiary",
              mb: theme.spacing(1.5),
            }}
          >
            Connections ({connections.length})
          </Typography>
          <Grid container spacing={1.5}>
            {connections.map((conn) => (
              <Grid item xs={12} sm={6} md={4} key={conn.id}>
                <IntegrationCard connection={conn} />
              </Grid>
            ))}
          </Grid>
        </Box>
      ) : (
        <Section
          variant="panel"
          sx={{
            mb: theme.spacing(3),
            px: theme.spacing(2.5),
            py: theme.spacing(2),
          }}
        >
          <Box display="flex" alignItems="center" gap={theme.spacing(2)}>
            <Iconify
              icon="solar:chart-2-linear"
              width={20}
              sx={{ color: "text.disabled", flexShrink: 0 }}
            />
            <Box flex={1} minWidth={0}>
              <Typography
                sx={{
                  typography: "s1",
                  fontWeight: "fontWeightMedium",
                  color: "text.primary",
                }}
              >
                No integrations connected
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {offersIssues
                  ? "Export request logs and metrics, or send alerts and issues to an external platform."
                  : "Export request logs and metrics, or send alerts to an external platform."}
              </Typography>
            </Box>
          </Box>
        </Section>
      )}

      {/* Available Platforms */}
      <Typography
        sx={{
          typography: "s2",
          fontWeight: "fontWeightMedium",
          color: "text.tertiary",
          mb: theme.spacing(1.5),
        }}
      >
        Available Platforms
      </Typography>
      <Grid container spacing={1.5}>
        {platforms.map((platform) => {
          // A card opens the wizard for another connection; it also says
          // when the platform already has one or more, as a warning when
          // one of them is paused, failing or in a state not known here.
          const own = connections.filter((c) => c.platform === platform.id);
          const connected = own.length;
          const working = own.every((c) => WORKING_STATUSES.has(c.status));
          return (
            <Grid item xs={12} sm={6} md={4} key={platform.id}>
              <IntegrationAdminOnly allowed={canManage} block>
                <Section
                  variant="panel"
                  sx={{ opacity: platform.available === false ? 0.5 : 1 }}
                  disabled={!canManage || platform.available === false}
                  onClick={() => selectPlatform(platform.id)}
                >
                  <Box
                    display="flex"
                    alignItems="center"
                    gap={theme.spacing(1.5)}
                  >
                    <PlatformLogo platform={platform.id} size={28} />
                    <Box flex={1} minWidth={0}>
                      <Box
                        display="flex"
                        flexWrap="wrap"
                        alignItems="center"
                        gap={theme.spacing(1)}
                      >
                        <Typography
                          sx={{
                            typography: "s2_1",
                            fontWeight: "fontWeightSemiBold",
                            color: "text.primary",
                          }}
                        >
                          {platform.name}
                        </Typography>
                        {connected > 0 && (
                          <RunStatusBadge
                            status="connected"
                            role={working ? "success" : "warning"}
                            label={
                              connected === 1
                                ? "Connected"
                                : `${connected} connected`
                            }
                          />
                        )}
                        {platform.available === false && (
                          <Chip
                            label="Coming Soon"
                            size="small"
                            variant="outlined"
                          />
                        )}
                      </Box>
                      <Typography
                        sx={{ typography: "s2", color: "text.tertiary" }}
                      >
                        {platform.description}
                      </Typography>
                    </Box>
                    {canManage && platform.available !== false && (
                      <Iconify
                        icon="solar:alt-arrow-right-linear"
                        width={16}
                        height={16}
                        sx={{ color: "text.disabled" }}
                      />
                    )}
                  </Box>
                </Section>
              </IntegrationAdminOnly>
            </Grid>
          );
        })}
      </Grid>

      <WebhooksSection workspaceRouteId={workspaceRouteId} />

      {/* Wizard Dialog */}
      <AddIntegrationWizard
        open={wizardOpen}
        platforms={platforms}
        onClose={() => setSelectedPlatform(null)}
        initialPlatform={selectedPlatform || undefined}
        onSelectSlack={() => {
          setSelectedPlatform(null);
          connectSlack();
        }}
      />
    </Box>
  );
}

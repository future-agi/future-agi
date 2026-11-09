import React, { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  Card,
  CardActionArea,
  Grid,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { LoadingScreen } from "src/components/loading-screen";
import {
  integrationKeys,
  useIntegrationConnections,
  useStartSlackInstall,
} from "src/api/integrations";
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
  const { data, isLoading, isError } = useIntegrationConnections({
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

  if (isError) {
    return (
      <Box py={4}>
        <Alert severity="error">
          Failed to load integrations. Please try again later.
        </Alert>
      </Box>
    );
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
      <Box
        display="flex"
        justifyContent="space-between"
        alignItems="center"
        mb={theme.spacing(3)}
      >
        <Box>
          <Typography
            sx={{
              typography: "m2",
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
            }}
          >
            Integrations
          </Typography>
          <Typography
            sx={{
              typography: "s1",
              fontWeight: "fontWeightRegular",
              color: "text.primary",
              mt: theme.spacing(0.5),
            }}
          >
            Connect external platforms
          </Typography>
        </Box>
        <IntegrationAdminOnly allowed={canManage}>
          <Button
            variant="contained"
            color="primary"
            size="small"
            startIcon={<Iconify icon="solar:add-circle-bold" />}
            onClick={() => setSelectedPlatform("")}
            disabled={!canManage}
            sx={{ fontWeight: 500 }}
          >
            Add Integration
          </Button>
        </IntegrationAdminOnly>
      </Box>

      {/* Active Connections */}
      {connections.length > 0 ? (
        <Box mb={theme.spacing(4)}>
          <Typography
            sx={{
              typography: "s2",
              fontWeight: "fontWeightMedium",
              color: "text.secondary",
              mb: theme.spacing(2),
            }}
          >
            Connections ({connections.length})
          </Typography>
          <Grid container spacing={2}>
            {connections.map((conn) => (
              <Grid item xs={12} sm={6} md={4} key={conn.id}>
                <IntegrationCard connection={conn} />
              </Grid>
            ))}
          </Grid>
        </Box>
      ) : (
        <Card
          variant="outlined"
          sx={{
            mb: theme.spacing(4),
            p: theme.spacing(4),
            textAlign: "center",
            borderStyle: "dashed",
          }}
        >
          <Typography
            sx={{
              typography: "m3",
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
            }}
            gutterBottom
          >
            No integrations connected
          </Typography>
          <Typography
            sx={{
              typography: "s1",
              color: "text.secondary",
              mb: theme.spacing(2),
            }}
          >
            {offersIssues
              ? "Export request logs and metrics, or send alerts and issues to an external platform."
              : "Export request logs and metrics, or send alerts to an external platform."}
          </Typography>
          <IntegrationAdminOnly allowed={canManage}>
            <Button
              variant="contained"
              color="primary"
              size="small"
              startIcon={<Iconify icon="solar:add-circle-bold" />}
              onClick={() => setSelectedPlatform("")}
              disabled={!canManage}
              sx={{ fontWeight: 500 }}
            >
              Add Integration
            </Button>
          </IntegrationAdminOnly>
        </Card>
      )}

      {/* Available Platforms */}
      <Typography
        sx={{
          typography: "s2",
          fontWeight: "fontWeightMedium",
          color: "text.secondary",
          mb: theme.spacing(2),
        }}
      >
        Available Platforms
      </Typography>
      <Grid container spacing={2}>
        {platforms.map((platform) => (
          <Grid item xs={12} sm={6} md={4} key={platform.id}>
            <IntegrationAdminOnly allowed={canManage} block>
              <Card variant="outlined">
                <CardActionArea
                  disabled={!canManage}
                  onClick={() => selectPlatform(platform.id)}
                  sx={{ p: theme.spacing(2.5) }}
                >
                  <Box
                    display="flex"
                    alignItems="center"
                    gap={theme.spacing(2)}
                  >
                    <PlatformLogo platform={platform.id} size={40} />
                    <Box flex={1}>
                      <Typography
                        sx={{
                          typography: "s1",
                          fontWeight: "fontWeightMedium",
                          color: "text.primary",
                        }}
                      >
                        {platform.name}
                      </Typography>
                      <Typography
                        sx={{ typography: "s2", color: "text.disabled" }}
                      >
                        {platform.description}
                      </Typography>
                    </Box>
                    {canManage && (
                      <Iconify
                        icon="octicon:chevron-right-24"
                        width={16}
                        height={16}
                        sx={{ color: "text.disabled" }}
                      />
                    )}
                  </Box>
                </CardActionArea>
              </Card>
            </IntegrationAdminOnly>
          </Grid>
        ))}
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

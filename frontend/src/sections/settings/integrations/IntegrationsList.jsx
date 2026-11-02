import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  Card,
  CardActionArea,
  Chip,
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
import { PLATFORMS } from "./constants";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { getErrorMessage } from "./utils";
import { paths } from "src/routes/paths";
import {
  useIntegrations,
  installedRepos,
  githubAccountsLabel,
  connectedCodingAgent,
} from "./workspace-tools/store";
import BrandMark from "./workspace-tools/BrandMark";
import { StatusPill } from "./workspace-tools/StatusPill";
import {
  SLACK_RETURN_TO_KEY,
  isSlackActive,
  isSlackPagePath,
  slackConnectionOf,
  withoutSlack,
} from "./workspace-tools/slackConnection";

export default function IntegrationsList() {
  const { role } = useAuthContext();
  const theme = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data, isLoading, isError } = useIntegrationConnections();
  const { mutate: startSlackInstall, isPending: connectingSlack } =
    useStartSlackInstall();
  const autoStartedSlack = useRef(false);
  const [slackError, setSlackError] = useState("");
  const [slackNotice, setSlackNotice] = useState("");
  // null = wizard closed, "" = wizard open (no platform), "langfuse" = wizard open with platform
  const [selectedPlatform, setSelectedPlatform] = useState(null);

  const wizardOpen = selectedPlatform !== null;
  const allConnections = Array.isArray(data) ? data : [];
  /* Slack sits under Code & collaboration. Only the connection it shows leaves the
     observability list; any other Slack connection stays reachable there. */
  const slackConnection = slackConnectionOf(allConnections);
  const connections = allConnections.filter((c) => c !== slackConnection);
  const observabilityPlatforms = withoutSlack(PLATFORMS, "id");
  const slackOn = isSlackActive(slackConnection);
  const integrations = useIntegrations();
  const codingAgent = connectedCodingAgent(integrations);
  const workspaceMatch = location.pathname.match(
    /^\/dashboard\/settings\/workspace\/([^/]+)\/integrations\/?$/,
  );
  const toolPath = (id) =>
    workspaceMatch
      ? `${paths.dashboard.settings.workspaceIntegrations(workspaceMatch[1])}/${id}`
      : `${paths.dashboard.settings.integrations}/${id}`;
  /* Workspace tools: code and team chat. Connected once here, used by
     Agents, Simulate and Alerts. */
  const workspaceTools = [
    {
      id: "github",
      name: "GitHub",
      desc: integrations.github
        ? `Installed on ${githubAccountsLabel(integrations)} · ${installedRepos(integrations).length} repositor${installedRepos(integrations).length === 1 ? "y" : "ies"}`
        : "Connect agents from code and open fixes as pull requests",
      on: !!integrations.github,
    },
    {
      id: "slack",
      name: "Slack",
      desc: slackOn
        ? [slackConnection.display_name || "Slack", integrations.slackDefaultChannel]
            .filter(Boolean)
            .join(" · ")
        : "Notifications and alerts in your team's channels",
      on: slackOn,
    },
    {
      id: "coding-agents",
      name: "Coding agents",
      brand: "coding-agents",
      desc: "Let Claude Code, Codex or Cursor fix issues. Future AGI checks every change.",
      on: !!codingAgent,
    },
  ];

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (params.get("slack") === "connected") {
      queryClient.invalidateQueries({ queryKey: integrationKeys.connections() });
      const returnTo = sessionStorage.getItem(SLACK_RETURN_TO_KEY);
      sessionStorage.removeItem(SLACK_RETURN_TO_KEY);
      if (isSlackPagePath(returnTo)) {
        navigate(`${returnTo}?slack=connected`, { replace: true });
      } else if (returnTo?.startsWith("/") && !returnTo.startsWith("//")) {
        navigate(`${returnTo}${returnTo.includes("?") ? "&" : "?"}resume_alert=1`, { replace: true });
      } else {
        setSlackNotice("Slack workspace connected successfully.");
      }
    } else if (params.get("slack") === "error") {
      const returnTo = sessionStorage.getItem(SLACK_RETURN_TO_KEY);
      sessionStorage.removeItem(SLACK_RETURN_TO_KEY);
      if (isSlackPagePath(returnTo)) {
        navigate(`${returnTo}?slack=error`, { replace: true });
      } else {
        setSlackError("Could not connect Slack. Please try again.");
      }
    }
  }, [location.search, navigate, queryClient]);

  const connectSlack = useCallback(() => {
    setSlackError("");
    const params = new URLSearchParams(location.search);
    const returnTo = params.get("return_to");
    if (returnTo?.startsWith("/") && !returnTo.startsWith("//")) {
      sessionStorage.setItem(SLACK_RETURN_TO_KEY, returnTo);
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
  }, [location.search, startSlackInstall]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (
      params.get("platform") === "slack" &&
      !params.has("slack") &&
      !isLoading &&
      !autoStartedSlack.current
    ) {
      autoStartedSlack.current = true;
      connectSlack();
    }
  }, [connectSlack, isLoading, location.search]);

  const selectPlatform = (platformId) => {
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
        <Alert severity="success" sx={{ mb: 2 }}>
          {slackNotice}
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
            Connect your code, your team&apos;s chat and the observability
            platforms you already use
          </Typography>
        </Box>
        <Button
          variant="contained"
          color="primary"
          size="small"
          startIcon={<Iconify icon="solar:add-circle-bold" />}
          onClick={() => setSelectedPlatform("")}
          disabled={!RolePermission.API_KEYS[PERMISSIONS.CREATE][role]}
          sx={{ fontWeight: 500 }}
        >
          Add Integration
        </Button>
      </Box>

      {/* Code & collaboration */}
      <Box mb={theme.spacing(4)}>
        <Typography
          sx={{
            typography: "s2",
            fontWeight: "fontWeightMedium",
            color: "text.secondary",
            mb: theme.spacing(2),
          }}
        >
          Code &amp; collaboration
        </Typography>
        <Grid container spacing={2}>
          {workspaceTools.map((tool) => (
            <Grid item xs={12} sm={6} md={4} key={tool.id}>
              <Card
                variant="outlined"
                data-testid={`workspace-tool-${tool.id}`}
              >
                <CardActionArea
                  onClick={() => navigate(toolPath(tool.id))}
                  sx={{ p: theme.spacing(2.5) }}
                >
                  <Box
                    display="flex"
                    alignItems="center"
                    gap={theme.spacing(2)}
                  >
                    <BrandMark brand={tool.brand || tool.id} size={40} />
                    <Box flex={1} minWidth={0}>
                      <Box
                        display="flex"
                        alignItems="center"
                        gap={theme.spacing(1)}
                      >
                        <Typography
                          sx={{
                            typography: "s1",
                            fontWeight: "fontWeightMedium",
                            color: "text.primary",
                          }}
                        >
                          {tool.name}
                        </Typography>
                        <StatusPill on={tool.on} size="small" />
                      </Box>
                      <Typography
                        noWrap
                        sx={{ typography: "s2", color: "text.disabled" }}
                      >
                        {tool.desc}
                      </Typography>
                    </Box>
                    <Iconify
                      icon="octicon:chevron-right-24"
                      width={16}
                      height={16}
                      sx={{ color: "text.disabled" }}
                    />
                  </Box>
                </CardActionArea>
              </Card>
            </Grid>
          ))}
        </Grid>
      </Box>

      {/* Observability platforms: the backend connections. Scoped by its own
          heading so its empty state can't read as "nothing is connected"
          while GitHub and Slack above are installed. */}
      <Box data-testid="observability-platforms">
        <Typography
          sx={{
            typography: "s2",
            fontWeight: "fontWeightMedium",
            color: "text.secondary",
            mb: theme.spacing(2),
          }}
        >
          Observability platforms
        </Typography>
        {connections.length > 0 ? (
          <Box mb={theme.spacing(3)}>
            <Typography
              sx={{
                typography: "s3",
                fontWeight: "fontWeightMedium",
                color: "text.disabled",
                mb: theme.spacing(1.5),
              }}
            >
              Connected ({connections.length})
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
              mb: theme.spacing(3),
              px: theme.spacing(2.5),
              py: theme.spacing(2),
              borderStyle: "dashed",
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
                  No observability platforms connected yet
                </Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  Connect Langfuse, Datadog and others to bring traces, spans
                  and evaluations into Future AGI.
                </Typography>
              </Box>
              <Button
                variant="outlined"
                size="small"
                startIcon={<Iconify icon="solar:add-circle-linear" />}
                onClick={() => setSelectedPlatform("")}
                disabled={!RolePermission.API_KEYS[PERMISSIONS.CREATE][role]}
                sx={{ fontWeight: 500, flexShrink: 0 }}
              >
                Connect a platform
              </Button>
            </Box>
          </Card>
        )}

        {/* Available Platforms */}
        <Typography
          sx={{
            typography: "s3",
            fontWeight: "fontWeightMedium",
            color: "text.disabled",
            mb: theme.spacing(1.5),
          }}
        >
          Available platforms
        </Typography>
        <Grid container spacing={2}>
          {observabilityPlatforms.map((platform) => (
            <Grid item xs={12} sm={6} md={4} key={platform.id}>
              <Card
                variant="outlined"
                sx={{ opacity: platform.available ? 1 : 0.5 }}
              >
                <CardActionArea
                  disabled={!platform.available}
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
                      <Box
                        display="flex"
                        alignItems="center"
                        gap={theme.spacing(1)}
                      >
                        <Typography
                          sx={{
                            typography: "s1",
                            fontWeight: "fontWeightMedium",
                            color: "text.primary",
                          }}
                        >
                          {platform.name}
                        </Typography>
                        {!platform.available && (
                          <Chip
                            label="Coming Soon"
                            size="small"
                            variant="outlined"
                          />
                        )}
                      </Box>
                      <Typography
                        sx={{ typography: "s2", color: "text.disabled" }}
                      >
                        {platform.description}
                      </Typography>
                    </Box>
                    {platform.available && (
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
            </Grid>
          ))}
        </Grid>
      </Box>

      {/* Wizard Dialog */}
      <AddIntegrationWizard
        open={wizardOpen}
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

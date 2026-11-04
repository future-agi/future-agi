import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Alert, Box, Button, Chip, Grid, Typography, useTheme } from "@mui/material";
import ErrorState from "src/components/error-state";
import PageHeader from "src/components/page-header";
import Section from "src/components/section";
import Iconify from "src/components/iconify";
import { LoadingScreen } from "src/components/loading-screen";
import {
  integrationKeys,
  useIntegrationConnections,
  useCompleteGitHubInstall,
  useStartGitHubInstall,
  useStartSlackInstall,
} from "src/api/integrations";
import IntegrationCard from "./IntegrationCard";
import PlatformLogo from "./PlatformLogo";
import AddIntegrationWizard from "./AddIntegrationWizard";
import { GITHUB_INSTALL_OUTCOMES, INSTALL_FLOWS, PLATFORMS } from "./constants";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { getErrorMessage } from "./utils";

const SLACK_RETURN_TO_KEY = "slack-integration-return-to";
const GITHUB_RETURN_TO_KEY = "github-integration-return-to";
const isAppPath = (path) => path?.startsWith("/") && !path.startsWith("//");

export default function IntegrationsList() {
  const { role } = useAuthContext();
  const theme = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data, isLoading, isError } = useIntegrationConnections();
  const { mutate: startSlackInstall, isPending: connectingSlack } =
    useStartSlackInstall();
  const { mutate: startGitHubInstall, isPending: connectingGitHub } =
    useStartGitHubInstall();
  const { mutate: completeGitHubInstall } = useCompleteGitHubInstall();
  const completingGitHub = useRef(false);
  const autoStartedInstall = useRef(false);
  const [installMessage, setInstallMessage] = useState(null);
  // null = wizard closed, "" = wizard open (no platform), "langfuse" = wizard open with platform
  const [selectedPlatform, setSelectedPlatform] = useState(null);

  const wizardOpen = selectedPlatform !== null;
  const connections = Array.isArray(data) ? data : [];

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (params.get("slack") === "connected") {
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
        setInstallMessage({
          severity: "success",
          text: INSTALL_FLOWS.slack.connected,
        });
      }
    } else if (params.get("slack") === "error") {
      setInstallMessage({
        severity: "error",
        text: "Could not connect Slack. Please try again.",
      });
    }
    const githubOutcome = params.get("github");
    const finishGitHub = (outcome) => {
      queryClient.invalidateQueries({
        queryKey: integrationKeys.connections(),
      });
      const returnTo = sessionStorage.getItem(GITHUB_RETURN_TO_KEY);
      sessionStorage.removeItem(GITHUB_RETURN_TO_KEY);
      if (isAppPath(returnTo)) {
        navigate(
          `${returnTo}${returnTo.includes("?") ? "&" : "?"}github=${outcome}`,
          { replace: true },
        );
      } else {
        setInstallMessage(GITHUB_INSTALL_OUTCOMES[outcome]);
      }
    };
    if (githubOutcome === "complete" && !completingGitHub.current) {
      // GitHub sent the browser back with the install's state; the install is
      // finished here, as the signed-in user, so the parameters never bind
      // anything on their own.
      completingGitHub.current = true;
      const handoff = Object.fromEntries(
        ["state", "setup_action", "code", "installation_id"]
          .filter((key) => params.get(key))
          .map((key) => [key, params.get(key)]),
      );
      navigate(location.pathname, { replace: true });
      completeGitHubInstall(handoff, {
        onSuccess: (response) => finishGitHub(response.data?.result?.outcome),
        onError: (error) =>
          setInstallMessage({
            severity: "error",
            text: getErrorMessage(
              error,
              "Could not connect GitHub. Please try again.",
            ),
          }),
        onSettled: () => {
          completingGitHub.current = false;
        },
      });
    } else if (GITHUB_INSTALL_OUTCOMES[githubOutcome]) {
      finishGitHub(githubOutcome);
    } else if (githubOutcome === "error") {
      setInstallMessage({
        severity: "error",
        text: "Could not connect GitHub. Please try again.",
      });
    }
  }, [
    location.search,
    location.pathname,
    navigate,
    queryClient,
    completeGitHubInstall,
  ]);

  const startInstall = useCallback(
    (platformId) => {
      const flow = INSTALL_FLOWS[platformId];
      setInstallMessage(null);
      const returnTo = new URLSearchParams(location.search).get("return_to");
      if (isAppPath(returnTo)) {
        sessionStorage.setItem(
          platformId === "github" ? GITHUB_RETURN_TO_KEY : SLACK_RETURN_TO_KEY,
          returnTo,
        );
      }
      const start =
        platformId === "github" ? startGitHubInstall : startSlackInstall;
      start(undefined, {
        onSuccess: (response) => {
          const installUrl = response.data?.result?.[flow.urlField];
          if (!installUrl) {
            setInstallMessage({
              severity: "error",
              text: `Could not start ${flow.label} authorization. Please try again.`,
            });
            return;
          }
          window.location.assign(installUrl);
        },
        onError: (error) =>
          setInstallMessage({
            severity: "error",
            text: getErrorMessage(error, `Could not connect ${flow.label}.`),
          }),
      });
    },
    [location.search, startGitHubInstall, startSlackInstall],
  );

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const platformId = params.get("platform");
    if (
      INSTALL_FLOWS[platformId] &&
      !params.has(platformId) &&
      !isLoading &&
      !autoStartedInstall.current
    ) {
      autoStartedInstall.current = true;
      if (!RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role]) {
        setInstallMessage({
          severity: "info",
          text: `You don't have permission to connect ${INSTALL_FLOWS[platformId].label}.`,
        });
        return;
      }
      startInstall(platformId);
    }
  }, [startInstall, isLoading, location.search, role]);

  const selectPlatform = (platformId) => {
    if (INSTALL_FLOWS[platformId]) startInstall(platformId);
    else setSelectedPlatform(platformId);
  };

  if (isLoading) {
    return <LoadingScreen sx={{ height: "100%", minHeight: "60vh" }} />;
  }

  if (isError) {
    return (
      <ErrorState
        title="Failed to load integrations"
        detail="Please try again later."
        sx={{ height: "auto", flexGrow: 0, py: 4 }}
      />
    );
  }

  return (
    <Box>
      {installMessage && (
        <Alert severity={installMessage.severity} sx={{ mb: 2 }}>
          {installMessage.text}
        </Alert>
      )}
      {(connectingSlack || connectingGitHub) && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Starting {connectingGitHub ? "GitHub" : "Slack"} authorization…
        </Alert>
      )}
      {/* Header */}
      <PageHeader
        title="Integrations"
        description="Connect external observability platforms to import traces"
        sx={{ mb: 3 }}
        actions={
          <Button
            variant="contained"
            color="primary"
            size="small"
            startIcon={<Iconify icon="solar:add-circle-bold" />}
            onClick={() => setSelectedPlatform("")}
            disabled={!RolePermission.API_KEYS[PERMISSIONS.CREATE][role]}
          >
            Add Integration
          </Button>
        }
      />

      {/* Active Connections */}
      {connections.length > 0 ? (
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
                Connect external platforms to import traces, spans, and
                evaluations into Future AGI.
              </Typography>
            </Box>
            <Button
              variant="outlined"
              size="small"
              startIcon={<Iconify icon="solar:add-circle-linear" />}
              onClick={() => setSelectedPlatform("")}
              disabled={!RolePermission.API_KEYS[PERMISSIONS.CREATE][role]}
              sx={{ flexShrink: 0 }}
            >
              Add Integration
            </Button>
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
        {PLATFORMS.map((platform) => (
          <Grid item xs={12} sm={6} md={4} key={platform.id}>
            <Section
              variant="panel"
              sx={{ opacity: platform.available ? 1 : 0.5 }}
              disabled={!platform.available}
              onClick={() => selectPlatform(platform.id)}
            >
              <Box display="flex" alignItems="center" gap={theme.spacing(1.5)}>
                <PlatformLogo platform={platform.id} size={28} />
                <Box flex={1}>
                  <Box
                    display="flex"
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
                    {!platform.available && (
                      <Chip
                        label="Coming Soon"
                        size="small"
                        variant="outlined"
                      />
                    )}
                  </Box>
                  <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
                    {platform.description}
                  </Typography>
                </Box>
                {platform.available && (
                  <Iconify
                    icon="solar:alt-arrow-right-linear"
                    width={16}
                    height={16}
                    sx={{ color: "text.disabled" }}
                  />
                )}
              </Box>
            </Section>
          </Grid>
        ))}
      </Grid>

      {/* Wizard Dialog */}
      <AddIntegrationWizard
        open={wizardOpen}
        onClose={() => setSelectedPlatform(null)}
        initialPlatform={selectedPlatform || undefined}
        onSelectInstall={(platformId) => {
          setSelectedPlatform(null);
          startInstall(platformId);
        }}
      />
    </Box>
  );
}

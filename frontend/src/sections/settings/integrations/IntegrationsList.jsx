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
      startInstall(platformId);
    }
  }, [startInstall, isLoading, location.search]);

  const selectPlatform = (platformId) => {
    if (INSTALL_FLOWS[platformId]) startInstall(platformId);
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
            Connect external observability platforms to import traces
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
            Connect external platforms to import traces, spans, and evaluations
            into FutureAGI.
          </Typography>
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
        {PLATFORMS.map((platform) => (
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
                <Box display="flex" alignItems="center" gap={theme.spacing(2)}>
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

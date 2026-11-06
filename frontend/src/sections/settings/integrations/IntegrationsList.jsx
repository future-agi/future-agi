import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Alert, Box, Button, Chip, Grid, Typography, useTheme } from "@mui/material";
import ErrorState from "src/components/error-state";
import PageHeader from "src/components/page-header";
import Section from "src/components/section";
import Iconify from "src/components/iconify";
import { LoadingScreen } from "src/components/loading-screen";
import { integrationKeys, useIntegrationConnections, useStartSlackInstall } from "src/api/integrations";
import IntegrationCard from "./IntegrationCard";
import PlatformLogo from "./PlatformLogo";
import AddIntegrationWizard from "./AddIntegrationWizard";
import { PLATFORMS } from "./constants";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { getErrorMessage } from "./utils";

const SLACK_RETURN_TO_KEY = "slack-integration-return-to";

export default function IntegrationsList() {
  const { role } = useAuthContext();
  const theme = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data, isLoading, isError } = useIntegrationConnections();
  const { mutate: startSlackInstall, isPending: connectingSlack } = useStartSlackInstall();
  const autoStartedSlack = useRef(false);
  const [slackError, setSlackError] = useState("");
  const [slackNotice, setSlackNotice] = useState("");
  // null = wizard closed, "" = wizard open (no platform), "langfuse" = wizard open with platform
  const [selectedPlatform, setSelectedPlatform] = useState(null);

  const wizardOpen = selectedPlatform !== null;
  const connections = Array.isArray(data) ? data : [];

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (params.get("slack") === "connected") {
      queryClient.invalidateQueries({ queryKey: integrationKeys.connections() });
      const returnTo = sessionStorage.getItem(SLACK_RETURN_TO_KEY);
      sessionStorage.removeItem(SLACK_RETURN_TO_KEY);
      if (returnTo?.startsWith("/") && !returnTo.startsWith("//")) {
        navigate(`${returnTo}${returnTo.includes("?") ? "&" : "?"}resume_alert=1`, { replace: true });
      } else {
        setSlackNotice("Slack workspace connected successfully.");
      }
    } else if (params.get("slack") === "error") {
      setSlackError("Could not connect Slack. Please try again.");
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
          setSlackError("Could not start Slack authorization. Please try again.");
          return;
        }
        window.location.assign(authorizationUrl);
      },
      onError: (error) => setSlackError(getErrorMessage(error, "Could not connect Slack.")),
    });
  }, [location.search, startSlackInstall]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (params.get("platform") === "slack" && !params.has("slack") && !isLoading && !autoStartedSlack.current) {
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
      <ErrorState
        title="Failed to load integrations"
        detail="Please try again later."
        sx={{ height: "auto", flexGrow: 0, py: 4 }}
      />
    );
  }

  return (
    <Box>
      {slackError && <Alert severity="error" sx={{ mb: 2 }}>{slackError}</Alert>}
      {slackNotice && <Alert severity="success" sx={{ mb: 2 }}>{slackNotice}</Alert>}
      {connectingSlack && <Alert severity="info" sx={{ mb: 2 }}>Starting Slack authorization…</Alert>}
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
        onSelectSlack={() => { setSelectedPlatform(null); connectSlack(); }}
      />
    </Box>
  );
}

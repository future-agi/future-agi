import ErrorState from "src/components/error-state";
import PageHeader from "src/components/page-header";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import {
  useIntegrations,
  installedRepos,
  githubAccountsLabel,
  connectedCodingAgent,
} from "src/sections/simulate-v2/_mock/integrations";
import BrandMark from "src/sections/simulate-v2/integrations/BrandMark";
import { StatusPill } from "src/sections/simulate-v2/integrations/GithubIntegrationPage";
import { Box, Button, Chip, Grid, Typography, useTheme } from "@mui/material";
import Section from "src/components/section";
import Iconify from "src/components/iconify";
import { LoadingScreen } from "src/components/loading-screen";
import { useIntegrationConnections } from "src/api/integrations";
import IntegrationCard from "./IntegrationCard";
import PlatformLogo from "./PlatformLogo";
import AddIntegrationWizard from "./AddIntegrationWizard";
import { PLATFORMS } from "./constants";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

export default function IntegrationsList() {
  const { role } = useAuthContext();
  const theme = useTheme();
  const { data, isLoading, isError } = useIntegrationConnections();
  const navigate = useNavigate();
  const integrations = useIntegrations();
  const codingAgent = connectedCodingAgent(integrations);
  /* Workspace tools — code and team chat. Connected once here, used by
     Agents, Simulate and Alerts. */
  const workspaceTools = [
    {
      id: "github",
      name: "GitHub",
      desc: integrations.github
        ? `Installed on ${githubAccountsLabel(integrations)} · ${installedRepos(integrations).length} repositor${installedRepos(integrations).length === 1 ? "y" : "ies"}`
        : "Connect agents from code and open fixes as pull requests",
      tags: ["Source code", "Pull requests"],
      on: !!integrations.github,
    },
    {
      id: "slack",
      name: "Slack",
      desc: integrations.slack
        ? `${integrations.slack.workspace} · ${integrations.slack.defaultChannel}`
        : "Notifications and alerts in your team's channels",
      tags: ["Notifications", "Alerts"],
      on: !!integrations.slack,
    },
    {
      id: "coding-agents",
      name: "IDE & coding agents",
      brand: codingAgent ? codingAgent.id : "coding-agents",
      desc: codingAgent
        ? `${codingAgent.label} · ${codingAgent.via === "plugin" ? "plugin" : "MCP server"}`
        : "Let Claude Code, Codex or Cursor fix issues — Future AGI checks every change",
      tags: ["Briefs", "Verdicts"],
      on: !!codingAgent,
      labels: ["Connected", "Not connected"],
    },
  ];
  // null = wizard closed, "" = wizard open (no platform), "langfuse" = wizard open with platform
  const [selectedPlatform, setSelectedPlatform] = useState(null);

  const wizardOpen = selectedPlatform !== null;
  const connections = Array.isArray(data) ? data : [];

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
      {/* Header */}
      <PageHeader
        title="Integrations"
        description="Connect your code, your team's chat and the observability platforms you already use"
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

      {/* Code & collaboration */}
      <Box mb={theme.spacing(4)}>
        <Typography
          sx={{
            typography: "s1",
            fontWeight: "fontWeightSemiBold",
            color: "text.primary",
            mb: theme.spacing(1.5),
          }}
        >
          Code &amp; collaboration
        </Typography>
        <Grid container spacing={1.5}>
          {workspaceTools.map((tool) => (
            <Grid item xs={12} sm={6} md={4} key={tool.id}>
              <Section
                variant="panel"
                onClick={() =>
                  navigate(
                    `${paths.dashboard.settings.integrations}/${tool.id}`,
                  )
                }
              >
                <Box
                  display="flex"
                  alignItems="center"
                  gap={theme.spacing(1.5)}
                >
                  <BrandMark brand={tool.brand || tool.id} size={28} />
                  <Box flex={1} minWidth={0}>
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
                        {tool.name}
                      </Typography>
                      <StatusPill
                        on={tool.on}
                        size="small"
                        labels={tool.labels}
                      />
                    </Box>
                    <Typography
                      noWrap
                      sx={{ typography: "s2", color: "text.tertiary" }}
                    >
                      {tool.desc}
                    </Typography>
                  </Box>
                  <Iconify
                    icon="solar:alt-arrow-right-linear"
                    width={16}
                    height={16}
                    sx={{ color: "text.disabled" }}
                  />
                </Box>
              </Section>
            </Grid>
          ))}
        </Grid>
      </Box>

      {/* Observability platforms — the backend connections. Scoped by its own
          heading so its empty state can't read as "nothing is connected"
          while GitHub and Slack above are installed. */}
      <Typography
        sx={{
          typography: "s1",
          fontWeight: "fontWeightSemiBold",
          color: "text.primary",
          mb: theme.spacing(1.5),
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
              color: "text.tertiary",
              mb: theme.spacing(1.5),
            }}
          >
            Connected ({connections.length})
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
                No observability platforms connected yet
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                Connect Langfuse, Datadog and others to bring traces, spans and
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
              Connect a platform
            </Button>
          </Box>
        </Section>
      )}

      {/* Available Platforms */}
      <Typography
        sx={{
          typography: "s3",
          fontWeight: "fontWeightMedium",
          color: "text.tertiary",
          mb: theme.spacing(1.5),
        }}
      >
        Available platforms
      </Typography>
      <Grid container spacing={1.5}>
        {PLATFORMS.map((platform) => (
          <Grid item xs={12} sm={6} md={4} key={platform.id}>
            <Section
              variant="panel"
              sx={{ opacity: platform.available ? 1 : 0.5 }}
              disabled={!platform.available}
              onClick={() => setSelectedPlatform(platform.id)}
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
      />
    </Box>
  );
}

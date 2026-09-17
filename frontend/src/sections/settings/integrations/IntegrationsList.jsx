import { useState } from "react";
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
  // null = wizard closed, "" = wizard open (no platform), "langfuse" = wizard open with platform
  const [selectedPlatform, setSelectedPlatform] = useState(null);

  const wizardOpen = selectedPlatform !== null;
  const connections = Array.isArray(data) ? data : [];

  if (isLoading) {
    return (
      <LoadingScreen sx={{ height: "100%", minHeight: "60vh" }} />
    );
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
                onClick={() => setSelectedPlatform(platform.id)}
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
      />
    </Box>
  );
}

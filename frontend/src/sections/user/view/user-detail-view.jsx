import React, { useEffect, useState } from "react";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import Stack from "@mui/material/Stack";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import Container from "@mui/material/Container";
import Grid from "@mui/material/Unstable_Grid2";
import { paths } from "src/routes/paths";
import { useRouter } from "src/routes/hooks";
import Iconify from "src/components/iconify";
import { useSettingsContext } from "src/components/settings";
import axios from "src/utils/axios";

export default function UserDetailView({ id }) {
  const settings = useSettingsContext();
  const router = useRouter();

  const [metrics, setMetrics] = useState({
    trace_count: 0,
    error_rate: 0,
    p95_latency: 0,
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const response = await axios.get(`/tracer/api/v1/users/${id}/metrics`);
        setMetrics(response.data);
      } catch (error) {
        console.error("Failed to fetch user metrics", error);
      } finally {
        setLoading(false);
      }
    };
    if (id) {
      fetchMetrics();
    }
  }, [id]);

  const handleBack = () => {
    router.push(paths.dashboard.users);
  };

  return (
    <Container maxWidth={settings.themeStretch ? false : "lg"}>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ mb: { xs: 3, md: 5 } }}
      >
        <Typography variant="h4">User Details</Typography>

        <Button
          onClick={handleBack}
          startIcon={<Iconify icon="eva:arrow-ios-back-fill" />}
        >
          Back to Users
        </Button>
      </Stack>

      <Grid container spacing={3}>
        <Grid xs={12} sm={6} md={4}>
          <Card
            sx={{
              p: 3,
              borderRadius: 2,
              color: "primary.dark",
              bgcolor: "primary.lighter",
              boxShadow: "0 8px 24px rgba(0,0,0,0.05)",
              transition: "transform 0.2s",
              "&:hover": { transform: "translateY(-4px)" }
            }}
          >
            <Stack spacing={1}>
              <Typography variant="subtitle2" sx={{ opacity: 0.72 }}>
                Trace Count
              </Typography>
              <Typography variant="h3">
                {loading ? "..." : metrics.trace_count}
              </Typography>
            </Stack>
          </Card>
        </Grid>

        <Grid xs={12} sm={6} md={4}>
          <Card
            sx={{
              p: 3,
              borderRadius: 2,
              color: "error.dark",
              bgcolor: "error.lighter",
              boxShadow: "0 8px 24px rgba(0,0,0,0.05)",
              transition: "transform 0.2s",
              "&:hover": { transform: "translateY(-4px)" }
            }}
          >
            <Stack spacing={1}>
              <Typography variant="subtitle2" sx={{ opacity: 0.72 }}>
                Error Rate
              </Typography>
              <Typography variant="h3">
                {loading ? "..." : `${metrics.error_rate}%`}
              </Typography>
            </Stack>
          </Card>
        </Grid>

        <Grid xs={12} sm={6} md={4}>
          <Card
            sx={{
              p: 3,
              borderRadius: 2,
              color: "info.dark",
              bgcolor: "info.lighter",
              boxShadow: "0 8px 24px rgba(0,0,0,0.05)",
              transition: "transform 0.2s",
              "&:hover": { transform: "translateY(-4px)" }
            }}
          >
            <Stack spacing={1}>
              <Typography variant="subtitle2" sx={{ opacity: 0.72 }}>
                p95 Latency
              </Typography>
              <Typography variant="h3">
                {loading ? "..." : `${metrics.p95_latency} ms`}
              </Typography>
            </Stack>
          </Card>
        </Grid>
      </Grid>
    </Container>
  );
}

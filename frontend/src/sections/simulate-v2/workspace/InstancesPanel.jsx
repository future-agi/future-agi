import PropTypes from "prop-types";
import { Box, Stack, Typography, Button, IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard, StatusDot, StatusChip, MetricTile, EmptyState } from "../components/primitives";
import { INSTANCES } from "../_mock/envConfig";

/**
 * Live sandboxes of this environment.
 *
 * "Fresh copy per task" is the claim the whole product rests on; this is where
 * you can see it actually happening — one instance per task, each with its own
 * uptime and resource use, and each killable on its own.
 */
export default function InstancesPanel({ env, onGo }) {
  const live = INSTANCES.filter((i) => ["running", "grading"].includes(i.status));
  const finished = INSTANCES.filter((i) => !["running", "grading"].includes(i.status));

  if (INSTANCES.length === 0) {
    return (
      <Box sx={{ p: 2 }}>
        <SectionCard>
          <EmptyState
            icon="solar:server-square-linear"
            title="No instances"
            body="Instances spin up when a simulation runs. Start one and they'll appear here."
            action={
              <Button variant="contained"
            color="primary" size="small" onClick={() => onGo("runs")}>
                Go to runs
              </Button>
            }
          />
        </SectionCard>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 2 }}>
      <Box sx={{ mb: 3 }}>
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>Instances</Typography>
        <Typography sx={{ typography: "s1", color: "text.secondary", maxWidth: 760 }}>
          Every task gets its own sandboxed copy of {env.name}. This is what that looks like
          while a run is in flight.
        </Typography>
      </Box>

      <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ mb: 2 }}>
        <MetricTile label="Live" value={live.length} sub="running or grading" color="#2563EB" icon="solar:server-square-linear" />
        <MetricTile label="Finished" value={finished.length} sub="this run" icon="solar:check-circle-linear" />
        <MetricTile label="Region" value="us-east-1" sub="all instances" icon="solar:global-linear" />
        <MetricTile label="Version" value="v7" sub="pinned for this run" icon="solar:code-square-linear" />
      </Stack>

      <SectionCard
        title={`Instances (${INSTANCES.length})`}
        action={
          live.length > 0 && (
            <Button
              size="small"
              color="error"
              startIcon={<Iconify icon="solar:stop-circle-linear" width={15} />}
              sx={{ typography: "s2", fontWeight: 600 }}
            >
              Stop all live
            </Button>
          )
        }
      >
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {INSTANCES.map((i) => {
            const isLive = ["running", "grading"].includes(i.status);
            return (
              <Stack key={i.id} direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.5 }}>
                <StatusDot status={i.status} size={7} />
                <Typography
                  sx={{ typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace", width: 130, flexShrink: 0 }}
                >
                  {i.id}
                </Typography>
                <Box flex={1} minWidth={0}>
                  <Typography noWrap sx={{ typography: "s2" }}>{i.task}</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    {i.version} · {i.region} · up {i.uptimeS}s
                  </Typography>
                </Box>

                {isLive && (
                  <Stack direction="row" spacing={1.5} sx={{ display: { xs: "none", lg: "flex" } }}>
                    <Meter label="cpu" value={i.cpu} />
                    <Meter label="mem" value={i.mem} />
                  </Stack>
                )}

                <StatusChip status={i.status} />

                {isLive && (
                  <Tooltip title="Stop instance" arrow>
                    <IconButton size="small">
                      <Iconify icon="solar:stop-circle-linear" width={16} sx={{ color: "#DC2626" }} />
                    </IconButton>
                  </Tooltip>
                )}
              </Stack>
            );
          })}
        </Stack>
      </SectionCard>
    </Box>
  );
}

InstancesPanel.propTypes = { env: PropTypes.object.isRequired, onGo: PropTypes.func };

function Meter({ label, value }) {
  const color = value > 80 ? "#DC2626" : value > 55 ? "#CA8A04" : "#16A34A";
  return (
    <Stack direction="row" alignItems="center" spacing={0.75} sx={{ width: 84 }}>
      <Typography sx={{ typography: "s3", color: "text.subtitle", width: 22 }}>{label}</Typography>
      <Box sx={{ flex: 1, height: 4, borderRadius: 2, bgcolor: "background.neutral", overflow: "hidden" }}>
        <Box sx={{ height: "100%", width: `${value}%`, bgcolor: color }} />
      </Box>
      <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
    </Stack>
  );
}
Meter.propTypes = { label: PropTypes.string, value: PropTypes.number };

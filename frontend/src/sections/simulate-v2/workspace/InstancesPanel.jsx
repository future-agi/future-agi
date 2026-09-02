import PropTypes from "prop-types";
import { Box, Stack, Typography, Button, IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard, StatusDot, StatusChip, MetricTile, EmptyState } from "../components/primitives";
import { instancesFor, masterSnapshot } from "../_mock/envConfig";
import { SHADOW_GUARANTEES } from "../_mock/sandbox";
import { alpha } from "@mui/material/styles";

/**
 * Live sandboxes of this environment.
 *
 * "Fresh copy per task" is the claim the whole product rests on, and a flat
 * list of instances did not show it — five boxes with the same version number
 * could equally be five long-lived servers. So the screen is a lineage: the
 * frozen master at the top, and every instance hanging off it as a copy that
 * was written to and then thrown away.
 *
 * What each copy wrote is the part worth reading. The master's row count never
 * moves; the writes live and die inside the copy, which is what makes two runs
 * comparable rather than sequential.
 */
export default function InstancesPanel({ env, envState, onGo }) {
  const master = masterSnapshot(env);
  /*
    Instances belong to a run. Showing five live copies for an environment that
    has never been run is a screenshot, not a state — and it is the one claim
    on this page (nothing touches your system, every task gets its own copy)
    that has to be believable to be worth making.
  */
  const lastRun = envState?.runs?.[0] || null;
  const instances = lastRun ? instancesFor(env, { active: false }) : [];
  const live = instances.filter((i) => ["running", "grading"].includes(i.status));
  const destroyed = instances.filter((i) => i.destroyed);

  if (instances.length === 0) {
    return (
      <Box sx={{ p: 2 }}>
        <SectionCard>
          <EmptyState
            icon="solar:server-square-linear"
            title="No instances"
            body="Instances are created per task when a simulation runs, and destroyed with everything they wrote. Run one and they'll appear here."
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
          Every task gets its own sandboxed copy of {env.name}. These are from run{" "}
          {lastRun.ordinal || 1} · agent {lastRun.agentVersion}, {new Date(lastRun.finishedAt).toLocaleString(undefined, {
            day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
          })}.
        </Typography>
      </Box>

      {/*
        The claim the whole product rests on, spelled out as mechanisms. A
        customer pointing a test harness at their own live system is the
        failure mode that ends a trial, so this is stated before the metrics.
      */}
      <SectionCard
        title="Shadow agent — never your production one"
        subtitle="What isolation actually means here"
        sx={{ mb: 2 }}
      >
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {SHADOW_GUARANTEES.map((g) => (
            <Stack key={g.id} direction="row" spacing={1.75} sx={{ px: 2.5, py: 1.5 }} alignItems="flex-start">
              <Box
                sx={{
                  width: 26, height: 26, borderRadius: 0.875, display: "grid", placeItems: "center", flexShrink: 0,
                  color: "#16A34A",
                  bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.16 : 0.1),
                }}
              >
                <Iconify icon={g.icon} width={14} />
              </Box>
              <Box minWidth={0}>
                <Typography sx={{ typography: "s2", fontWeight: 700 }}>{g.label}</Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>{g.note}</Typography>
              </Box>
            </Stack>
          ))}
        </Stack>
      </SectionCard>

      <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ mb: 2 }}>
        <MetricTile label="Copies taken" value={instances.length} sub={`from ${master.id}`} color="#2563EB" icon="solar:copy-linear" />
        <MetricTile label="Live" value={live.length} sub={live.length ? "running or grading" : "the run has finished"} icon="solar:server-square-linear" />
        <MetricTile label="Destroyed" value={destroyed.length} sub="with everything they wrote" icon="solar:trash-bin-trash-linear" />
        <MetricTile label="Master" value={master.version} sub={`pinned for run ${lastRun.ordinal || 1}`} icon="solar:lock-keyhole-linear" />
      </Stack>

      <SectionCard
        title="Lineage"
        subtitle="One frozen master, one copy per task"
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
        {/*
          The master. Read-only is stated on the row rather than in a caption,
          because the row is the thing being claimed about: this database was
          built once, and no run has ever written to it.
        */}
        <Stack
          direction="row" alignItems="center" spacing={2}
          sx={{ px: 2.5, py: 1.75, bgcolor: "background.neutral" }}
        >
          <Box
            sx={{
              width: 26, height: 26, borderRadius: 0.875, display: "grid", placeItems: "center", flexShrink: 0,
              color: "#7857FC",
              bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.18 : 0.1),
            }}
          >
            <Iconify icon="solar:lock-keyhole-bold" width={14} />
          </Box>
          <Box flex={1} minWidth={0}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography
                noWrap
                sx={{ typography: "s2", fontWeight: 700, fontFamily: "ui-monospace, Menlo, monospace" }}
              >
                {master.id}
              </Typography>
              <Typography
                sx={{
                  px: 0.75, py: 0.25, borderRadius: 0.5, flexShrink: 0,
                  typography: "s3", fontWeight: 700, color: "#7857FC",
                  bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.18 : 0.1),
                }}
              >
                FROZEN
              </Typography>
            </Stack>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {master.version} · {master.tables} tables · {master.rows.toLocaleString()} rows · {master.sizeMB} MB ·
              read-only, never written to by a run
            </Typography>
          </Box>
        </Stack>

        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {instances.map((i, n) => (
            <Copy key={i.id} instance={i} last={n === instances.length - 1} />
          ))}
        </Stack>
      </SectionCard>
    </Box>
  );
}

InstancesPanel.propTypes = {
  envState: PropTypes.object, env: PropTypes.object.isRequired, onGo: PropTypes.func };

/**
 * One copy, hung off the master by an elbow.
 *
 * The connector is doing real work here — without it the copies read as five
 * independent servers, which is the misreading the whole screen exists to fix.
 */
function Copy({ instance: i, last }) {
  const isLive = ["running", "grading"].includes(i.status);

  return (
    <Stack direction="row" alignItems="stretch" sx={{ pr: 2.5 }}>
      <Box
        sx={{
          width: 34, flexShrink: 0, position: "relative", ml: 2.5,
          "&::before": {
            content: '""', position: "absolute", left: 12, top: 0,
            bottom: last ? "50%" : 0, borderLeft: "1px dashed",
            borderColor: (t) => alpha(t.palette.text.disabled, 0.55),
          },
          "&::after": {
            content: '""', position: "absolute", left: 12, top: "50%", width: 14,
            borderTop: "1px dashed",
            borderColor: (t) => alpha(t.palette.text.disabled, 0.55),
          },
        }}
      />

      <Stack direction="row" alignItems="center" spacing={2} sx={{ flex: 1, minWidth: 0, py: 1.5 }}>
        <StatusDot status={i.status} size={7} />
        <Typography
          sx={{
            typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace",
            width: 120, flexShrink: 0, color: i.destroyed ? "text.subtitle" : "text.primary",
          }}
        >
          {i.id}
        </Typography>
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "s2" }}>{i.task}</Typography>
          {/* What it wrote, and what happened to those writes. */}
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            copy of {i.from} · wrote +{i.wroteRows} rows ·{" "}
            {i.destroyed ? "destroyed after grading, writes discarded" : `up ${i.uptimeS}s`}
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
    </Stack>
  );
}
Copy.propTypes = { instance: PropTypes.object, last: PropTypes.bool };

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

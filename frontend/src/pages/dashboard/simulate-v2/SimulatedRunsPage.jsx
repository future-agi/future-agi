import { useMemo } from "react";
import { Helmet } from "react-helmet-async";
import { useNavigate } from "react-router-dom";
import {
  Box, Stack, Typography, Button, Table, TableHead, TableBody, TableRow, TableCell, LinearProgress,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { SimStoreProvider, useSimStore } from "src/sections/simulate-v2/store";
import { StatusChip } from "src/sections/simulate-v2/components/primitives";

/**
 * Simulated Runs.
 *
 * One row per *run*, flattened across every environment — matches the
 * page name. An environment with zero runs doesn't appear here; it
 * lives on /environments (the picker + My Environments) instead. The
 * previous version listed envs and rendered "0/0" for freshly-created
 * ones, which made the page look empty even when the user had just
 * created something.
 *
 * Rows include in-progress runs (recorded at start, upserted on
 * completion) — a run that's live shows a pulsing progress bar and a
 * "Running" chip.
 */
export default function SimulatedRunsPage() {
  return (
    <SimStoreProvider>
      <Helmet>
        <title>Simulated Runs | Future AGI</title>
      </Helmet>
      <SimulatedRunsInner />
    </SimStoreProvider>
  );
}

function SimulatedRunsInner() {
  const navigate = useNavigate();
  const { state } = useSimStore();

  /* Flatten every run across every env, newest first. Each row carries
     its parent env so the click-through knows which workspace to open
     and the row can show which env the run belongs to. */
  const rows = useMemo(() => {
    const envs = state?.myEnvironments || [];
    const byEnv = state?.byEnv || {};
    const flat = [];
    envs.forEach((env) => {
      const runs = byEnv[env.id]?.runs || [];
      runs.forEach((r) => flat.push({ ...r, env }));
    });
    flat.sort((a, b) => {
      const at = new Date(a.finishedAt || a.startedAt || 0).getTime();
      const bt = new Date(b.finishedAt || b.startedAt || 0).getTime();
      return bt - at;
    });
    return flat;
  }, [state]);

  const totalRuns = rows.length;
  const runningCount = rows.filter((r) => r.status === "running").length;

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        overflow: "hidden",
        minHeight: 0,
        p: 2,
      }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "flex-end" }}
        spacing={2}
        sx={{ flexShrink: 0 }}
      >
        <Box>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              Simulated Runs
            </Typography>
            <Box
              sx={{
                px: 0.75,
                py: 0.25,
                borderRadius: "4px",
                bgcolor: "action.hover",
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography sx={{ fontSize: "11px", fontWeight: 600, color: "text.secondary" }}>
                {totalRuns}
              </Typography>
            </Box>
            {runningCount > 0 && (
              <Typography sx={{ typography: "s3", color: "primary.main", fontWeight: 600 }}>
                · {runningCount} running
              </Typography>
            )}
          </Stack>
          <Typography sx={{ typography: "s1", color: "text.secondary", mt: 0.5 }}>
            Every simulation run across your environments. Click a row to open the run.
          </Typography>
        </Box>
        <Button
          variant="contained"
          color="primary"
          onClick={() => navigate(paths.dashboard.simulate.environments)}
          startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          sx={{ typography: "s1", fontWeight: 600, flexShrink: 0 }}
        >
          Create a simulation
        </Button>
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
        {rows.length === 0 ? (
          <EmptyState onCreate={() => navigate(paths.dashboard.simulate.environments)} />
        ) : (
          <Table stickyHeader size="small">
            <TableHead>
              <TableRow>
                <HeaderCell>Run</HeaderCell>
                <HeaderCell>Environment</HeaderCell>
                <HeaderCell>Status</HeaderCell>
                <HeaderCell align="right">Pass</HeaderCell>
                <HeaderCell align="right">Agent</HeaderCell>
                <HeaderCell align="right">Started</HeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r) => (
                <RunRow key={`${r.env.id}-${r.id}`} run={r} onOpen={() => navigate(paths.dashboard.simulate.simulationRun(r.env.id, r.id))} />
              ))}
            </TableBody>
          </Table>
        )}
      </Box>
    </Box>
  );
}

function HeaderCell({ children, align }) {
  return (
    <TableCell
      align={align}
      sx={{
        typography: "s3",
        fontWeight: 700,
        color: "text.subtitle",
        textTransform: "uppercase",
        letterSpacing: 0.4,
        bgcolor: "background.paper",
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      {children}
    </TableCell>
  );
}

function RunRow({ run, onOpen }) {
  const isRunning = run.status === "running";
  const pct = run.total > 0 ? Math.round((run.passed / run.total) * 100) : 0;
  const startedAt = run.startedAt ? new Date(run.startedAt) : null;
  const startedLabel = startedAt ? startedAt.toLocaleString() : "—";
  return (
    <TableRow
      hover
      onClick={onOpen}
      sx={{ cursor: "pointer" }}
    >
      <TableCell sx={{ typography: "s2", fontWeight: 600 }}>
        {run.label || `Run ${run.ordinal || ""}`.trim()}
        {run.ordinal != null && (
          <Box component="span" sx={{ color: "text.subtitle", fontWeight: 500, ml: 0.5 }}>
            #{run.ordinal}
          </Box>
        )}
      </TableCell>
      <TableCell sx={{ typography: "s2", color: "text.secondary" }}>
        {run.env?.name}
      </TableCell>
      <TableCell>
        <StatusChip
          status={
            isRunning
              ? "running"
              : run.total > 0 && run.passed === run.total
                ? "passed"
                : "failed"
          }
        />
      </TableCell>
      <TableCell align="right">
        {isRunning ? (
          <Box sx={{ minWidth: 80 }}>
            <LinearProgress
              sx={{
                height: 4,
                borderRadius: 2,
                bgcolor: "action.hover",
                "& .MuiLinearProgress-bar": { bgcolor: "primary.main" },
              }}
            />
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
              — of {run.total}
            </Typography>
          </Box>
        ) : (
          <Stack alignItems="flex-end">
            <Typography sx={{ typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
              {run.passed}/{run.total}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{pct}%</Typography>
          </Stack>
        )}
      </TableCell>
      <TableCell align="right" sx={{ typography: "s2", color: "text.secondary" }}>
        {run.agentVersion || "—"}
      </TableCell>
      <TableCell align="right" sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "nowrap" }}>
        {startedLabel}
      </TableCell>
    </TableRow>
  );
}

function EmptyState({ onCreate }) {
  return (
    <Stack alignItems="center" justifyContent="center" sx={{ height: "100%", p: 6, textAlign: "center" }}>
      <Iconify icon="solar:play-circle-linear" width={34} sx={{ color: "text.subtitle", mb: 1.25 }} />
      <Typography sx={{ typography: "s1", fontWeight: 700 }}>No simulation runs yet</Typography>
      <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5, maxWidth: 420 }}>
        Once you create an environment and start a simulation, each run lands here — including runs mid-flight.
      </Typography>
      <Button
        variant="contained"
        color="primary"
        onClick={onCreate}
        startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
        sx={{ mt: 2, typography: "s2", fontWeight: 700 }}
      >
        Create a simulation
      </Button>
    </Stack>
  );
}

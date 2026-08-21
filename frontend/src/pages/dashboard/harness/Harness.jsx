import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { Helmet } from "react-helmet-async";
import Iconify from "src/components/iconify";
import {
  cancelHarnessJob,
  createHarnessJob,
  getHarnessJob,
  listHarnessJobs,
} from "src/api/harness/harness";

const terminalStages = new Set(["completed", "failed", "canceled"]);
const stages = [
  "understanding_agent",
  "generating_environment",
  "building_environment",
  "validating_environment",
  "generating_data",
  "generating_scenarios",
  "validating_scenarios",
  "connecting_agent",
  "running",
  "grading",
  "uploading_artifacts",
  "completed",
];

const readable = (value = "") =>
  value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());

function eventMessage(event) {
  const payload = event.payload || {};
  if (payload.detail) return String(payload.detail);
  if (payload.message) return String(payload.message);
  if (payload.stage) return `${readable(payload.stage)} ${readable(event.type)}`;
  return readable(event.type || "Progress updated");
}

export default function Harness() {
  const [sourcePath, setSourcePath] = useState("");
  const [scenarioCount, setScenarioCount] = useState(10);
  const [jobs, setJobs] = useState([]);
  const [current, setCurrent] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const refreshList = useCallback(async () => {
    try {
      const value = await listHarnessJobs();
      setJobs(Array.isArray(value) ? value : []);
      if (!current && value?.length) setCurrent(value[0]);
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || requestError.message);
    }
  }, [current]);

  useEffect(() => {
    refreshList();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const id = current?.job?.job_id;
    if (!id || terminalStages.has(current.status.stage)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const value = await getHarnessJob(id);
        setCurrent(value);
        setJobs((existing) =>
          existing.map((job) => (job.job?.job_id === id ? value : job)),
        );
      } catch (requestError) {
        setError(requestError?.response?.data?.detail || requestError.message);
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [current?.job?.job_id, current?.status?.stage]);

  const run = async () => {
    setSubmitting(true);
    setError("");
    try {
      const value = await createHarnessJob({
        source_path: sourcePath.trim(),
        scenario_count: Number(scenarioCount),
        connector: "auto",
      });
      setCurrent(value);
      setJobs((existing) => [value, ...existing]);
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || requestError.message);
    } finally {
      setSubmitting(false);
    }
  };

  const cancel = async () => {
    const value = await cancelHarnessJob(current.job.job_id);
    setCurrent(value);
  };

  const stageIndex = stages.indexOf(current?.status?.stage);
  const messages = useMemo(() => {
    const seen = new Set();
    return (current?.events || []).filter((event) => {
      const key = event.event_id || JSON.stringify(event);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [current?.events]);

  return (
    <>
      <Helmet><title>RL Environment | Future AGI</title></Helmet>
      <Box sx={{ height: "100%", display: "grid", gridTemplateColumns: "280px minmax(0, 1fr)", bgcolor: "background.default" }}>
        <Paper square variant="outlined" sx={{ p: 2, overflow: "auto" }}>
          <Typography variant="overline" color="text.secondary">Harness jobs</Typography>
          <Stack spacing={1} mt={1}>
            {jobs.map((item) => (
              <Button
                key={item.job.job_id}
                variant={current?.job?.job_id === item.job.job_id ? "contained" : "text"}
                color="inherit"
                onClick={() => setCurrent(item)}
                sx={{ justifyContent: "flex-start", textAlign: "left", textTransform: "none" }}
              >
                <Box><Typography variant="body2" fontWeight={600} noWrap>{item.job.metadata?.agent_name}</Typography><Typography variant="caption" color="text.secondary">{readable(item.status.stage)}</Typography></Box>
              </Button>
            ))}
          </Stack>
        </Paper>

        <Box sx={{ minWidth: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <Paper square variant="outlined" sx={{ p: 2 }}>
            <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} alignItems="center">
              <TextField fullWidth size="small" label="Local agent folder" placeholder="/absolute/path/to/agent" value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} />
              <TextField size="small" label="Scenarios" type="number" value={scenarioCount} onChange={(event) => setScenarioCount(event.target.value)} inputProps={{ min: 1, max: 100 }} sx={{ width: 120 }} />
              <Button variant="contained" disabled={submitting || !sourcePath.trim()} onClick={run} startIcon={submitting ? <CircularProgress size={16} /> : <Iconify icon="solar:play-bold" />}>Run end to end</Button>
            </Stack>
            {error && <Alert severity="error" sx={{ mt: 1.5 }}>{error}</Alert>}
          </Paper>

          {!current ? (
            <Stack flex={1} alignItems="center" justifyContent="center" spacing={1}>
              <Iconify icon="solar:server-square-cloud-linear" width={54} />
              <Typography variant="h6">Give us the agent folder; ALK does the rest.</Typography>
              <Typography color="text.secondary">Environment discovery, services, realistic data, scenarios, calls and grading run without operator prompts.</Typography>
            </Stack>
          ) : (
            <Box sx={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: "minmax(380px, 0.9fr) minmax(480px, 1.4fr)" }}>
              <Box sx={{ p: 2, overflow: "auto", borderRight: 1, borderColor: "divider" }}>
                <Stack direction="row" justifyContent="space-between" alignItems="center">
                  <Box><Typography variant="h6">{current.job.metadata?.agent_name}</Typography><Typography variant="caption" color="text.secondary">{current.job.run_id}</Typography></Box>
                  <Chip label={readable(current.status.stage)} color={current.status.stage === "failed" ? "error" : current.status.stage === "completed" ? "success" : "primary"} />
                </Stack>
                <Divider sx={{ my: 2 }} />
                <Stack spacing={1.2}>
                  {stages.map((stage, index) => {
                    const reached = stageIndex >= index || current.status.stage === "completed";
                    return <Stack key={stage} direction="row" spacing={1.2} alignItems="center"><Iconify icon={reached ? "solar:check-circle-bold" : "solar:record-circle-linear"} color={reached ? "success.main" : "text.disabled"} /><Typography color={reached ? "text.primary" : "text.disabled"}>{readable(stage)}</Typography></Stack>;
                  })}
                </Stack>
                {!terminalStages.has(current.status.stage) && <Button color="error" variant="outlined" onClick={cancel} sx={{ mt: 3 }}>Cancel run</Button>}
              </Box>

              <Box sx={{ p: 2, overflow: "auto" }}>
                <Typography variant="overline" color="text.secondary">Live harness activity</Typography>
                <Stack spacing={1.5} mt={1.5}>
                  <Paper variant="outlined" sx={{ p: 1.5, bgcolor: "action.hover" }}><Typography variant="body2">I’ll inspect the agent, build and seed its environment, create {current.job.scenario_count} diverse scenarios, run the calls, grade only observed behavior, and publish the results.</Typography></Paper>
                  {messages.map((event) => <Paper key={event.event_id} variant="outlined" sx={{ p: 1.5 }}><Typography variant="caption" color="primary.main">{readable(event.payload?.stage || event.type)}</Typography><Typography variant="body2">{eventMessage(event)}</Typography></Paper>)}
                  {current.status.detail && <Alert severity={current.status.stage === "failed" ? "error" : "info"}>{current.status.detail}</Alert>}
                  {!terminalStages.has(current.status.stage) && <Stack direction="row" spacing={1} alignItems="center"><CircularProgress size={16} /><Typography variant="body2" color="text.secondary">ALK is working autonomously…</Typography></Stack>}
                </Stack>
              </Box>
            </Box>
          )}
        </Box>
      </Box>
    </>
  );
}

import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import axios from "src/utils/axios";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";

const ACTIVE = new Set(["queued", "running"]);
const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const countsText = (name, counts) =>
  `${name}: A ${counts.a_wins} · B ${counts.b_wins} · Tie ${counts.ties}`;

function JudgeForm({ experimentId, runA, runB, readOnly, onClose }) {
  const root = `/model-hub/experiments/v2/${experimentId}/pairwise/`;
  const [version, setVersion] = useState("");
  const [job, setJob] = useState(null);
  const [summary, setSummary] = useState(null);
  const [offset, setOffset] = useState(0);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(null);
  const [savedOffset, setSavedOffset] = useState(0);
  const [savedRefresh, setSavedRefresh] = useState(0);
  const [savedLoading, setSavedLoading] = useState(true);
  const [savedError, setSavedError] = useState("");
  const epoch = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      epoch.current += 1;
    };
  }, []);

  const showError = (failure) => {
    const detail = getSafeActionErrorMessage(failure, "Try again.");
    setError(`Could not update the judge job. ${detail}`);
  };

  useEffect(() => {
    let stopped = false;
    setSavedLoading(true);
    setSavedError("");
    setSaved(null);
    const load = async () => {
      try {
        const response = await axios.get(`${root}judges/`, {
          params: { run_a: runA, run_b: runB, offset: savedOffset, limit: 20 },
        });
        if (!stopped) setSaved(response.data);
      } catch (failure) {
        if (!stopped) {
          const detail = getSafeActionErrorMessage(failure, "Try again.");
          setSavedError(`Could not load saved judgments. ${detail}`);
        }
      } finally {
        if (!stopped) setSavedLoading(false);
      }
    };
    load();
    return () => {
      stopped = true;
    };
  }, [root, runA, runB, savedOffset, savedRefresh]);

  const openSaved = (savedJob) => {
    if (busy) return;
    epoch.current += 1;
    setError("");
    setOffset(0);
    setSummary(null);
    setVersion(savedJob.judge_version);
    setJob(savedJob);
    setRefreshVersion((value) => value + 1);
  };

  useEffect(() => {
    if (!job?.id) return undefined;
    let stopped = false;
    let timer;
    const current = epoch.current;
    const refresh = async () => {
      try {
        const [detail, totals] = await Promise.all([
          axios.get(`${root}judges/${job.id}/`, {
            params: { offset, limit: 50 },
          }),
          axios.get(`${root}summary/`, {
            params: { run_a: runA, run_b: runB, judge_job: job.id },
          }),
        ]);
        if (stopped || current !== epoch.current) return;
        setJob(detail.data);
        setSummary(totals.data);
        setError("");
        if (
          ACTIVE.has(detail.data.status) ||
          (detail.data.counts?.running ?? 0) > 0
        ) {
          timer = setTimeout(refresh, 3000);
        }
      } catch (failure) {
        if (stopped || current !== epoch.current) return;
        showError(failure);
        timer = setTimeout(refresh, 5000);
      }
    };
    refresh();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [root, job?.id, job?.status, offset, runA, runB, refreshVersion]);

  const start = async () => {
    if (busy || readOnly) return;
    epoch.current += 1;
    setBusy(true);
    setError("");
    try {
      const response = await axios.post(`${root}judges/`, {
        run_a: runA,
        run_b: runB,
        judge_version: version.trim(),
      });
      if (!mounted.current) return;
      setOffset(0);
      setSummary(null);
      setJob(response.data);
    } catch (failure) {
      if (mounted.current) showError(failure);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  const cancel = async () => {
    if (busy || readOnly) return;
    epoch.current += 1;
    setBusy(true);
    try {
      const response = await axios.post(`${root}judges/${job.id}/cancel/`, {});
      if (mounted.current) {
        setJob((previous) => ({ ...previous, ...response.data }));
      }
    } catch (failure) {
      if (mounted.current) showError(failure);
    } finally {
      if (mounted.current) {
        setBusy(false);
        setRefreshVersion((value) => value + 1);
      }
    }
  };

  const orientedWinner = (winner) => {
    if (job?.run_a === runA || winner === "tie" || !winner) return winner;
    return winner === "a" ? "b" : "a";
  };
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Pairwise judge</DialogTitle>
      {error && <Alert severity="error">{error}</Alert>}
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <Typography variant="body2">
            Compare all saved rows, not just selected rows. Both runs must be
            completed on the same dataset version with matching examples and
            trials. Each row uses a recorded random presentation order. Normal
            evaluator usage applies.
          </Typography>
          <Typography variant="body2">A (baseline): {runA}</Typography>
          <Typography variant="body2">B (candidate): {runB}</Typography>
          <Typography variant="subtitle2">Saved judgments</Typography>
          {savedLoading && <Typography>Loading saved judgments…</Typography>}
          {savedError && (
            <Alert
              severity="error"
              action={
                <Button onClick={() => setSavedRefresh((value) => value + 1)}>
                  Retry saved judgments
                </Button>
              }
            >
              {savedError}
            </Alert>
          )}
          {saved?.jobs.length === 0 && (
            <Typography>No saved judgments for this pair.</Typography>
          )}
          {(saved?.jobs ?? []).map((savedJob) => (
            <Button
              key={savedJob.id}
              disabled={busy}
              onClick={() => openSaved(savedJob)}
            >
              Open judge {savedJob.judge_version} · {savedJob.status}
            </Button>
          ))}
          <Stack direction="row" spacing={1}>
            <Button
              disabled={savedLoading || savedOffset === 0}
              onClick={() => setSavedOffset(savedOffset - 20)}
            >
              Previous jobs
            </Button>
            <Button
              disabled={savedLoading || saved?.next_offset == null}
              onClick={() => setSavedOffset(saved.next_offset)}
            >
              Next jobs
            </Button>
          </Stack>
          <TextField
            label="Judge version ID"
            value={version}
            onChange={(event) => setVersion(event.target.value)}
            disabled={busy || readOnly || Boolean(job)}
            helperText="Use a saved LLM judge version with input, output_a, output_b and single A/B/tie choices."
          />
          {!job && (
            <Button
              onClick={start}
              disabled={
                busy ||
                readOnly ||
                !runA ||
                !runB ||
                runA === runB ||
                !UUID.test(version.trim())
              }
            >
              Start judge job
            </Button>
          )}
          {job && (
            <>
              <Typography>Status: {job.status}</Typography>
              <Typography variant="body2">
                {job.counts?.completed ?? 0}/{job.total} completed ·{" "}
                {job.counts?.failed ?? 0} failed ·{" "}
                {job.counts?.cancelled ?? 0} cancelled
              </Typography>
              {summary && (
                <>
                  <Typography>{countsText("Human", summary.human)}</Typography>
                  <Typography>{countsText("Judge", summary.judge)}</Typography>
                  <Typography>
                    {summary.agreement.rate === null
                      ? "Agreement: no comparable human votes yet"
                      : `Agreement: ${Math.round(summary.agreement.rate * 100)}% (${summary.agreement.matches}/${summary.agreement.compared} votes)`}
                  </Typography>
                  <Typography variant="caption">
                    Agreement compares each human vote with a completed
                    single-trial judge verdict. Repeated trials are excluded.
                  </Typography>
                </>
              )}
              {(job.items ?? []).map((item) => (
                <Stack
                  key={`${item.example_id}-${item.trial_number}`}
                  spacing={0.5}
                >
                  <Typography variant="body2">
                    {item.example_id} · Trial {item.trial_number} ·{" "}
                    {orientedWinner(item.winner)?.toUpperCase() || item.status}
                  </Typography>
                  <Typography variant="caption">
                    Presented {item.presentation_order.toUpperCase()} relative to
                    the recorded job pair ({job.run_a}, {job.run_b})
                  </Typography>
                  {item.reason && <Typography>{item.reason}</Typography>}
                  {item.error && (
                    <Alert severity="warning">
                      {item.error.replaceAll("_", " ")}
                    </Alert>
                  )}
                </Stack>
              ))}
              <Stack direction="row" spacing={1}>
                <Button
                  disabled={offset === 0}
                  onClick={() => setOffset(offset - 50)}
                >
                  Previous rows
                </Button>
                <Button
                  disabled={job.next_offset == null}
                  onClick={() => setOffset(job.next_offset)}
                >
                  Next rows
                </Button>
              </Stack>
              {ACTIVE.has(job.status) && (
                <Button onClick={cancel} disabled={busy || readOnly}>
                  Cancel judge
                </Button>
              )}
              <Typography variant="caption">
                Cancellation stops unstarted rows. A call already admitted can
                finish and its verdict and usage are retained. Open this job
                later from Saved judgments without starting another job.
              </Typography>
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}

export default function PairwiseJudgeDialog(props) {
  if (!props.open || !isSurfaceShown("exp.pairwiseJudge")) return null;
  return (
    <JudgeForm
      key={`${props.experimentId}:${props.runA}:${props.runB}`}
      {...props}
    />
  );
}

const props = {
  experimentId: PropTypes.string.isRequired,
  runA: PropTypes.string,
  runB: PropTypes.string,
  readOnly: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
};
JudgeForm.propTypes = props;
PairwiseJudgeDialog.propTypes = { ...props, open: PropTypes.bool };

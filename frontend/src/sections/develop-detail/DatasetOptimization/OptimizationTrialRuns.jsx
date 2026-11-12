// W6 C6: "Trials are runs" on an optimization's detail. Each trial is a run
// in the experiment the optimization created (link to its results), the
// chart shows each trial's score and the best score so far, and Promote best
// publishes the best trial's prompt as a new Prompts version and pins its
// run as the dataset baseline (the existing commit and baseline doors).
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Chip, Link, Stack, Typography } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import {
  bestTrialOf,
  budgetLine,
  chartPoints,
  draftVersionOf,
  lessonLine,
  promoteBodies,
} from "./trialRunsModel";

const OptimizationTrialRuns = ({ optimization }) => {
  const w6 = optimization?.w6;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [promoted, setPromoted] = useState(null);
  const [busy, setBusy] = useState(false);
  const points = useMemo(() => chartPoints(w6), [w6]);
  const best = bestTrialOf(w6);
  if (!w6) return null;
  const openExperiment = () =>
    navigate(`/dashboard/develop/experiment/${w6.experiment_id}/data`);
  const promptOf = (trialId) =>
    (optimization.table ?? []).find((r) => r.id === trialId)?.prompt ?? "";

  const promote = async () => {
    if (!best) return;
    setBusy(true);
    try {
      const b = promoteBodies(
        w6,
        best,
        promptOf(best.trial_id),
        w6.experiment_name,
      );
      const draft = await axios.post(
        endpoints.develop.runPrompt.addDraftInPrompt(w6.prompt.id),
        b.draft,
      );
      const version = draftVersionOf(draft);
      await axios.post(
        endpoints.develop.runPrompt.commitSavePrompt(w6.prompt.id),
        b.commit(version),
      );
      await axios.put(
        endpoints.develop.datasetRuns.baseline(w6.dataset_id),
        b.baseline,
      );
      setPromoted({ version, label: best.label });
      queryClient.invalidateQueries();
      enqueueSnackbar(
        `Published ${w6.prompt.name} ${version}; ${best.label} is the dataset baseline`,
        {
          variant: "success",
        },
      );
    } catch (e) {
      enqueueSnackbar(e?.response?.data?.result ?? "Promote failed", {
        variant: "error",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Stack
      spacing={1.5}
      data-testid="optimization-trial-runs"
      sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 2 }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        spacing={2}
      >
        <Box>
          <Typography typography="m3" fontWeight="fontWeightSemiBold">
            Trials are runs in{" "}
            <Link
              component="button"
              onClick={openExperiment}
              data-testid="optimization-experiment-link"
            >
              {w6.experiment_name}
            </Link>
          </Typography>
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="optimization-budget"
          >
            {w6.algorithm} · {budgetLine(w6)}
          </Typography>
        </Box>
        {promoted ? (
          <Chip
            color="success"
            variant="outlined"
            data-testid="optimization-promoted"
            label={`Published ${w6.prompt.name} ${promoted.version} · ${promoted.label} is the baseline`}
          />
        ) : best && optimization.status === "completed" ? (
          <LoadingButton
            variant="contained"
            size="small"
            loading={busy}
            onClick={promote}
            data-testid="optimization-promote"
          >
            Promote best ({best.label}, Trial {best.trial})
          </LoadingButton>
        ) : best ? null : (
          <Typography typography="s2" color="text.secondary">
            No trial beats the baseline
          </Typography>
        )}
      </Stack>
      <Box sx={{ height: 220 }} data-testid="optimization-best-so-far">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={points}
            margin={{ top: 8, right: 16, bottom: 0, left: 0 }}
          >
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" tick={{ fontSize: 11 }} />
            <YAxis unit="%" domain={["auto", "auto"]} tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend />
            <Line
              type="linear"
              dataKey="score"
              name="Trial score"
              stroke="#7857FC"
              dot
            />
            <Line
              type="stepAfter"
              dataKey="best"
              name="Best so far"
              stroke="#1BA672"
              strokeWidth={2}
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </Box>
      <Stack spacing={0.75}>
        {[
          {
            ...w6.baseline,
            trial: 0,
            lessons: [],
            best_so_far: w6.baseline.score,
          },
          ...w6.trials,
        ].map((t) => (
          <Stack
            key={t.run_id}
            direction="row"
            spacing={1.5}
            alignItems="baseline"
            data-testid="optimization-trial-row"
          >
            <Typography typography="s2" sx={{ width: 64 }}>
              {t.trial ? `Trial ${t.trial}` : "Baseline"}
            </Typography>
            <Button
              size="small"
              variant="text"
              onClick={openExperiment}
              sx={{ minWidth: 0, p: 0 }}
            >
              {t.label}
            </Button>
            <Typography typography="s2" sx={{ width: 120 }}>
              {(t.score * 100).toFixed(1)}% · best{" "}
              {(t.best_so_far * 100).toFixed(1)}%
            </Typography>
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ width: 80 }}
            >
              {t.status}
            </Typography>
            <Typography typography="s3" color="text.secondary" sx={{ flex: 1 }}>
              {t.trial
                ? t.lessons.map(lessonLine).join(" · ")
                : `${w6.prompt.name} ${w6.prompt.version} as it is`}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Stack>
  );
};

OptimizationTrialRuns.propTypes = {
  optimization: PropTypes.object,
};

export default OptimizationTrialRuns;

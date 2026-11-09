import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import DecisionCallPanel from "src/sections/common/DecisionJudges/DecisionCallPanel";
import TypedScoreDetails from "src/sections/common/EvalTypes/TypedScoreDetails";
import CustomTooltip from "src/components/tooltip";
import axios, { endpoints } from "src/utils/axios";
import { copyToClipboard } from "src/utils/utils";
import ReasonText from "src/sections/common/SafeMarkdown/ReasonText";
import { fToNowStrict } from "src/utils/format-time";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { useAuthContext } from "src/auth/hooks";
import DirectionIcon from "src/sections/common/Metrics/DirectionIcon";
import RunChip from "src/sections/common/Runs/RunChip";
import {
  formatValue,
  heatColor,
} from "src/sections/common/Metrics/metricFormat";
import { runResultsKey } from "src/api/develop/run-results";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import { inputsUsedLines } from "./inputsUsedModel";
import OpenInEvalsLink from "src/sections/common/EvalHandoffs/OpenInEvalsLink";

const sd = (xs) => {
  if (xs.length < 2) return 0;
  const m = xs.reduce((a, b) => a + b, 0) / xs.length;
  return Math.sqrt(xs.reduce((s, x) => s + (x - m) ** 2, 0) / (xs.length - 1));
};

const Title = ({ children }) => (
  <Typography
    typography="s3"
    color="text.disabled"
    sx={{ textTransform: "uppercase", letterSpacing: "0.5px" }}
  >
    {children}
  </Typography>
);
Title.propTypes = { children: PropTypes.node };

/** One trial value chip in the `Mean | 1 | 2 | 3` row. */
const TrialValue = ({ label, def, value, active, onClick }) => (
  <Box
    role="button"
    tabIndex={0}
    onClick={onClick}
    sx={{
      px: 1,
      py: 0.5,
      borderRadius: 0.5,
      border: "1px solid",
      borderColor: active ? "primary.main" : "divider",
      bgcolor: heatColor(def, value) ?? "transparent",
      cursor: onClick ? "pointer" : "default",
      minWidth: 56,
    }}
  >
    <Typography typography="s3" color="text.secondary" display="block">
      {label}
    </Typography>
    <Typography
      typography="s2"
      fontWeight={600}
      sx={{ fontVariantNumeric: "tabular-nums" }}
    >
      {formatValue(def, value, { agg: label === "Mean" ? "avg" : "value" })}
    </Typography>
  </Box>
);
TrialValue.propTypes = {
  label: PropTypes.string,
  def: PropTypes.object,
  value: PropTypes.number,
  active: PropTypes.bool,
  onClick: PropTypes.func,
};

/**
 * ScoreCard (PRODUCT-SPEC §3.11): one eval on one run and row. Score,
 * threshold and direction · reason (copy) · eval version and model · eval
 * trace · trials `Mean | 1 | 2 | 3` with σ · human override written through
 * experiments/v2/{id}/feedback/. L7c g7 (score:override): no "use this
 * correction to improve the eval" retune: nothing retunes an eval from it.
 */
const ScoreCard = ({
  def,
  run,
  color,
  result,
  trial,
  onTrialChange,
  uemId,
  readOnly,
  onClose,
}) => {
  const queryClient = useQueryClient();
  const { user } = useAuthContext();
  const score = result?.scores?.[def.key];
  const trials = (result?.trials_detail ?? []).filter(
    (t) => t.scores?.[def.key],
  );
  const current = trials[trial - 1] ?? trials[0];
  const trialScore = current?.scores?.[def.key];
  const values = trials
    .map((t) => t.scores[def.key].value)
    .filter(Number.isFinite);
  const override = score?.override;
  const passFail = def.output_type === "pass_fail";
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(passFail ? "Failed" : "");
  const [comment, setComment] = useState("");
  // The judge's own trace of this try's score (its prompt, verdict, model,
  // tokens and cost), opened in the trace drawer.
  const [evalTraceOpen, setEvalTraceOpen] = useState(false);
  const evalTraceId = trialScore?.eval_trace_id ?? score?.eval_trace_id ?? null;

  const submit = useMutation({
    mutationFn: async () => {
      const created = await axios.post(
        endpoints.develop.experiment.feedback.create(run.experiment_id),
        {
          value: passFail ? value : String(value),
          explanation: comment,
          user_eval_metric: uemId,
          source: "experiment",
          source_id: run.run_id,
          row_id: result.result_id?.split(":")[1],
        },
      );
      return created;
    },
    onSuccess: () => {
      enqueueSnackbar("Correction saved", { variant: "success" });
      setEditing(false);
      setComment("");
      queryClient.invalidateQueries({
        queryKey: runResultsKey(run.dataset_id),
      });
      queryClient.invalidateQueries({ queryKey: ["develop", "run-results"] });
    },
    onError: (e) =>
      enqueueSnackbar(
        getRequestErrorMessage(e, "Could not save the correction"),
        {
          variant: "error",
        },
      ),
  });

  // options-truth L3 G2 (evals:edit / evals:empty-reference): the run's own
  // pass bar and judge (what the scorer used), and the skip wording the
  // grid uses.
  const shown = trialScore ?? score;
  // L7c g6 (cell:inputs-used): this try's reads, else the cell's.
  const readInputs = shown?.inputs_used ?? score?.inputs_used ?? null;
  const skipped =
    !override &&
    (shown?.status === "skipped" ||
      (shown && shown.value == null && /^Skipped/.test(shown.reason ?? "")));
  const threshold = skipped
    ? null
    : def.output_type === "score"
      ? shown?.threshold ?? score?.threshold ?? def.pass_threshold ?? null
      : passFail
        ? "Pass"
        : null;
  const reason = trialScore?.reason ?? score?.reason;
  const evalVersion = trialScore?.evaluator_version ?? score?.evaluator_version;
  const valueShown = override?.value ?? score?.value;
  const judgeModel =
    shown?.judge_model ?? score?.judge_model ?? def.judge_model ?? null;
  const judgeCost = shown?.judge_cost_usd ?? score?.judge_cost_usd ?? null;
  const skipText = skipped
    ? /^Skipped/.test(shown?.reason ?? "")
      ? shown.reason
      : `Skipped · ${String(shown?.skip_reason ?? "no expected value").replace(/_/g, " ")}`
    : null;

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 1.5,
        bgcolor: "background.paper",
      }}
    >
      <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 1.25 }}>
        <Typography typography="s1" fontWeight="fontWeightSemiBold">
          {def.name}
        </Typography>
        <DirectionIcon higherIsBetter={def.higher_is_better} />
        <RunChip run={run} color={color} showStatus={false} />
        <Box sx={{ flex: 1 }} />
        <IconButton
          size="small"
          aria-label="Close score card"
          onClick={onClose}
        >
          <Iconify icon="mdi:close" width={16} />
        </IconButton>
      </Stack>

      <Stack
        direction="row"
        gap={2}
        alignItems="flex-end"
        sx={{ mb: 1.5, flexWrap: "wrap" }}
      >
        <Box>
          <Title>Score</Title>
          <CustomTooltip
            show={Boolean(judgeModel) && !skipped}
            title={
              judgeModel
                ? `Scored by judge ${judgeModel}${
                    judgeCost
                      ? ` · $${Number(judgeCost).toPrecision(2)} a call`
                      : ""
                  }`
                : ""
            }
            size="small"
            type="black"
          >
            <Typography
              typography={skipped ? "s1" : "m3"}
              fontWeight={600}
              data-testid="score-card-value"
              sx={{
                fontVariantNumeric: "tabular-nums",
                px: 0.75,
                borderRadius: 0.5,
                bgcolor: skipped
                  ? "transparent"
                  : heatColor(def, valueShown) ?? "transparent",
                color: skipped ? "text.secondary" : undefined,
                display: "inline-block",
              }}
            >
              {skipText ??
                (score?.display && !override
                  ? score.display
                  : formatValue(def, valueShown, {
                      agg: values.length > 1 ? "avg" : "value",
                    }))}
            </Typography>
          </CustomTooltip>
        </Box>
        {threshold != null && (
          <Box>
            <Title>Pass mark</Title>
            <Typography typography="s1">
              {passFail ? "Pass" : `≥ ${threshold}`}
            </Typography>
          </Box>
        )}
        <Box>
          <Title>Direction</Title>
          <Typography typography="s1">
            {def.higher_is_better === false
              ? "Lower is better"
              : "Higher is better"}
          </Typography>
        </Box>
        <Box>
          <Title>Eval</Title>
          <Typography typography="s1">
            {`${def.name} ${evalVersion ?? ""}${
              judgeModel ? ` · judge ${judgeModel}` : ""
            }`.replace("  ", " ")}
          </Typography>
        </Box>
      </Stack>

      {/* W1: a decision judge's own call on this score */}
      {(shown?.decision ?? score?.decision) && (
        <Box sx={{ mb: 1.25 }}>
          <DecisionCallPanel decision={shown?.decision ?? score?.decision} />
        </Box>
      )}

      {/* integration S7: a typed eval's own details (children, path, call) */}
      <TypedScoreDetails cell={shown?.typed ? shown : score} />

      {override && (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{
            mb: 1.25,
            px: 1,
            py: 0.75,
            bgcolor: "action.hover",
            borderRadius: 0.5,
          }}
        >
          <Iconify
            icon="mdi:account-outline"
            width={16}
            sx={{ color: "text.secondary" }}
          />
          <Typography typography="s2">
            {`Human: ${formatValue(def, override.value, { agg: "value" })} (was ${score?.display ?? formatValue(def, score?.value, { agg: "value" })}) · ${user?.name ?? "You"} · ${Date.now() - Date.parse(override.at) < 60000 ? "just now" : fToNowStrict(override.at)}`}
          </Typography>
        </Stack>
      )}

      {trials.length > 1 && (
        <Box sx={{ mb: 1.25 }}>
          <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 0.5 }}>
            <Title>Trials</Title>
            <Typography typography="s3" color="text.secondary">
              {`σ ${sd(values).toFixed(2)}${score?.flaky ? " · flaky" : ""}`}
            </Typography>
          </Stack>
          <Stack direction="row" gap={0.75} flexWrap="wrap">
            <TrialValue
              label="Mean"
              def={def}
              value={values.reduce((a, b) => a + b, 0) / values.length}
            />
            {trials.map((t, i) => (
              <TrialValue
                key={t.trial}
                label={`${t.trial}`}
                def={def}
                value={t.scores[def.key].value}
                active={i + 1 === trial}
                onClick={() => onTrialChange?.(i + 1)}
              />
            ))}
          </Stack>
        </Box>
      )}

      {reason && (
        <Box sx={{ mb: 1.25 }}>
          <Stack direction="row" alignItems="center" gap={0.5}>
            <Title>
              {trials.length > 1
                ? `Reason · trial ${current?.trial ?? 1}`
                : "Reason"}
            </Title>
            <IconButton
              size="small"
              aria-label="Copy reason"
              onClick={() => {
                copyToClipboard(reason);
                enqueueSnackbar("Reason copied", { variant: "success" });
              }}
              sx={{ p: 0.25 }}
            >
              <Iconify icon="basil:copy-outline" width={14} />
            </IconButton>
          </Stack>
          <ReasonText text={reason} />
          {trialScore?.error_localization && (
            <Typography typography="s2" color="text.secondary" sx={{ mt: 0.5 }}>
              {`Error localization: ${trialScore.error_localization}`}
            </Typography>
          )}
        </Box>
      )}

      {inputsUsedLines(readInputs).length > 0 && (
        <Box sx={{ mb: 1.25 }} data-testid="score-inputs-used">
          <Title>What it read</Title>
          {inputsUsedLines(readInputs).map((line) => (
            <Typography
              key={line}
              typography="s2"
              color="text.secondary"
              sx={{ wordBreak: "break-word" }}
            >
              {line}
            </Typography>
          ))}
        </Box>
      )}

      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <CustomTooltip
          show={!evalTraceId}
          size="small"
          placement="top"
          arrow
          title="No eval trace was recorded for this score"
        >
          <span>
            <Button
              size="small"
              variant="text"
              disabled={!evalTraceId}
              onClick={() => setEvalTraceOpen(true)}
              data-testid="score-card-eval-trace"
              endIcon={<Iconify icon="mdi:arrow-top-right" width={14} />}
              sx={{ typography: "s2" }}
            >
              Eval trace
            </Button>
          </span>
        </CustomTooltip>
        <OpenInEvalsLink
          datasetId={run?.dataset_id}
          metricKey={def.key}
          def={def}
          name={def.name}
          version={evalVersion}
        />
        <Box sx={{ flex: 1 }} />
        {!readOnly && !editing && (
          <Button
            size="small"
            variant="outlined"
            startIcon={<Iconify icon="mdi:pencil-outline" width={14} />}
            onClick={() => setEditing(true)}
            disabled={!uemId}
          >
            {override ? "Correct again" : "Override"}
          </Button>
        )}
      </Stack>

      {editing && (
        <Stack
          spacing={1.25}
          sx={{
            mt: 1.25,
            pt: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack direction="row" gap={1}>
            {passFail ? (
              <Select
                size="small"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                sx={{ minWidth: 140 }}
              >
                <MenuItem value="Passed">Pass</MenuItem>
                <MenuItem value="Failed">Fail</MenuItem>
              </Select>
            ) : (
              <TextField
                size="small"
                placeholder="Score 0–1"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                inputProps={{
                  inputMode: "decimal",
                  "aria-label": "Corrected score",
                }}
                sx={{ width: 140 }}
              />
            )}
            <TextField
              size="small"
              fullWidth
              placeholder="Why the eval is wrong (optional)"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              inputProps={{ "aria-label": "Comment" }}
            />
          </Stack>
          <Stack direction="row" justifyContent="flex-end" gap={1}>
            <Button size="small" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={
                submit.isPending ||
                (!passFail && !Number.isFinite(Number(value)))
              }
              onClick={() => submit.mutate()}
            >
              Save correction
            </Button>
          </Stack>
        </Stack>
      )}
      {evalTraceOpen && evalTraceId ? (
        <TraceDetailDrawerV2
          traceId={evalTraceId}
          open={evalTraceOpen}
          onClose={() => setEvalTraceOpen(false)}
        />
      ) : null}
    </Box>
  );
};

ScoreCard.propTypes = {
  def: PropTypes.object.isRequired,
  run: PropTypes.object.isRequired,
  color: PropTypes.object,
  result: PropTypes.object,
  trial: PropTypes.number,
  onTrialChange: PropTypes.func,
  uemId: PropTypes.string,
  readOnly: PropTypes.bool,
  onClose: PropTypes.func,
};

export default ScoreCard;

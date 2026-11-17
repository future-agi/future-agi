import React, { useState } from "react";
import PropTypes from "prop-types";
import { Button, Stack, TextField, Tooltip, Typography } from "@mui/material";
import { useAgreement } from "./alignmentApi";
import TunedJudgeCheck from "./TunedJudgeCheck";
import {
  TRAIN_DONE,
  leakActionsOf,
  leftOutTestRows,
  sharesReason,
  splitParams,
  trainingFilesOf,
  trainingLines,
  trainingPlanOf,
} from "./trainingSetExport";

const tabular = { fontVariantNumeric: "tabular-nums" };
const EMPTY = { train: "", validation: "", test: "", seed: "" };

const download = (file) => {
  const url = URL.createObjectURL(new Blob([file.data], { type: file.mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = file.name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

/**
 * Train a smaller judge (W7-2, owner decision 2: export, no trainer): the
 * labelled rows split into train / validation / test files, then the held-out
 * check of a tuned judge on the test rows before it is used. The check runs
 * on the definition the tuned judge would be saved on: this eval's current
 * version, or, for a built-in rule, its editable copy's (on the built-in's
 * labels), so the version's checked_on describes that version.
 */
const TrainJudgePanel = ({
  templateId,
  templateName,
  mode,
  editableCopy,
  report,
}) => {
  const [form, setForm] = useState(EMPTY);
  const [shares, setShares] = useState(null);
  const [downloaded, setDownloaded] = useState(false);
  const [leaveOut, setLeaveOut] = useState({});
  const reason = sharesReason(form);
  const base = { source: report.source_key, ...splitParams(shares) };
  const prepared = useAgreement(
    templateId,
    { ...base, include: "training" },
    { enabled: Boolean(shares) },
  );
  const training = prepared.data?.training ?? null;
  const plan = training ? trainingPlanOf(training, leaveOut) : null;
  const excluded = leftOutTestRows(plan);
  const files = plan ? trainingFilesOf(training, plan) : [];
  const testN = (training?.split?.test?.length ?? 0) - excluded.length;
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  // A rule kind that reads no judge (W7-SCOPE §2.6) has no judge to train.
  if (!mode) return null;
  return (
    <Stack gap={1.5} data-testid="w7-train">
      <Typography typography="s1" fontWeight={600}>
        Train a smaller judge
      </Typography>
      <Typography typography="s3" color="text.secondary">
        Export these labelled rows as a training set: the judge&apos;s inputs
        and each reviewer&apos;s verdict (and note) as OpenAI fine-tuning JSONL.
        Tune a model with your provider, then check it here on the test rows
        before it judges anything.
      </Typography>
      <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
        {[
          ["train", "Train %"],
          ["validation", "Validation %"],
          ["test", "Test %"],
          ["seed", "Seed"],
        ].map(([k, label]) => (
          <TextField
            key={k}
            size="small"
            label={label}
            value={form[k]}
            onChange={set(k)}
            sx={{ width: 110 }}
            inputProps={{ "data-testid": `w7-train-${k}` }}
          />
        ))}
        <Tooltip title={reason ?? ""}>
          <span>
            <Button
              size="small"
              variant="outlined"
              disabled={Boolean(reason)}
              onClick={() => {
                setShares({ ...form });
                setLeaveOut({});
                setDownloaded(false);
              }}
              data-testid="w7-train-prepare"
            >
              Prepare files
            </Button>
          </span>
        </Tooltip>
      </Stack>
      {reason ? (
        <Typography typography="s3" color="text.secondary">
          {reason}
        </Typography>
      ) : null}
      {plan ? (
        <Stack gap={0.75} data-testid="w7-train-plan">
          {trainingLines(plan).map((l) => (
            <Typography key={l} typography="s2" sx={tabular}>
              {l}
            </Typography>
          ))}
          {leakActionsOf(plan).map((a) => (
            <Button
              key={a.key}
              size="small"
              onClick={() => setLeaveOut((o) => ({ ...o, [a.key]: true }))}
              sx={{ alignSelf: "flex-start" }}
              data-testid={`w7-train-leave-out-${a.key}`}
            >
              {a.label}
            </Button>
          ))}
          <Typography typography="s3" color="text.secondary">
            System message: the eval&apos;s{" "}
            {training.instructions_from === "prompt" ? "prompt" : "description"}{" "}
            (“{training.instructions}”). Split seeded with {training.seed},
            stratified by the reviewers&apos; verdicts.
          </Typography>
          <Stack direction="row" gap={1} flexWrap="wrap">
            {files.map((f) => (
              <Button
                key={f.key}
                size="small"
                variant="outlined"
                onClick={() => {
                  download(f);
                  setDownloaded(true);
                }}
                data-testid={`w7-train-file-${f.key}`}
              >
                {f.name} · {f.lines} lines
              </Button>
            ))}
          </Stack>
          {downloaded ? (
            <Typography typography="s2" data-testid="w7-train-done">
              {TRAIN_DONE}
            </Typography>
          ) : null}
        </Stack>
      ) : null}
      {plan ? (
        <TunedJudgeCheck
          templateId={templateId}
          templateName={templateName}
          mode={mode}
          editableCopy={editableCopy}
          report={report}
          shares={shares}
          base={base}
          excluded={excluded}
          testN={testN}
          plan={plan}
          planKey={JSON.stringify([shares, leaveOut])}
        />
      ) : null}
    </Stack>
  );
};

TrainJudgePanel.propTypes = {
  templateId: PropTypes.string.isRequired,
  templateName: PropTypes.string,
  mode: PropTypes.string,
  editableCopy: PropTypes.object,
  report: PropTypes.object.isRequired,
};

export default TrainJudgePanel;

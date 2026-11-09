import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useSnackbar } from "notistack";
import { useQueryClient } from "@tanstack/react-query";
import { useEvalVersions } from "src/sections/evals/hooks/useEvalVersions";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import {
  editableCopyOf,
  useCustomPrices,
  useImprove,
  useRefreshAgreement,
} from "./alignmentApi";
import ImproveResult from "./ImproveResult";
import ImproveCandidates from "./ImproveCandidates";
import {
  COPY_INSTRUCTIONS,
  copyNameOf,
  improveConfirm,
  savedToast,
  splitReason,
} from "./agreementModel";

const tabular = { fontVariantNumeric: "tabular-nums" };

/**
 * Improve judge (W7-SCOPE §7 B2) on one label source: seeded split, rule-
 * based candidate edits from the drafting rows, the draft and the current
 * definition scored on the labelled rows, Save as v<N+1>. A reference-kind
 * built-in first gets an editable copy (`<name>_aligned`, reused once made).
 * "Current" is the version the eval scores with now (its newest saved one),
 * so a second round starts from what the first saved.
 */
const ImproveJudgePanel = ({
  templateId,
  templateName,
  mode,
  editableCopy,
  report,
}) => {
  const queryClient = useQueryClient();
  const refreshAgreement = useRefreshAgreement();
  const { enqueueSnackbar } = useSnackbar();
  const { customPrices, refresh: refreshPrices } = useCustomPrices();
  const [target, setTarget] = useState(() =>
    mode === "improve"
      ? { id: templateId, name: templateName }
      : editableCopy
        ? { id: editableCopy.id, name: editableCopy.name }
        : null,
  );
  const [copyError, setCopyError] = useState(null);
  const [share, setShare] = useState("");
  const [seed, setSeed] = useState("");
  const [plan, setPlan] = useState(null);
  const [ticked, setTicked] = useState(() => new Set());
  const [result, setResult] = useState(null);
  // The step 3 confirm: `{instructions?}` while open (an edited draft
  // carries its text), null when closed.
  const [confirm, setConfirm] = useState(null);
  const [saved, setSaved] = useState(null);
  const [draftText, setDraftText] = useState("");
  const improve = useImprove(target?.id);
  // The page's own versions read (useEvalVersions: one query per template).
  const { data: versionsData } = useEvalVersions(target?.id);
  const nextVersion = versionsData
    ? Math.max(
        0,
        ...(versionsData.versions ?? []).map((v) => v.version_number ?? 0),
      ) + 1
    : null;
  // A version saved after the draft was scored (e.g. a tuned judge) is the
  // current one now: saving this draft would drop it, so it is scored again.
  const movedTo =
    result?.current?.version != null &&
    nextVersion != null &&
    !saved &&
    nextVersion - 1 !== result.current.version
      ? nextVersion - 1
      : null;
  const reason = splitReason(share, seed);
  const n = (report.labelled_rows ?? []).length;
  // The judge of the current definition (its newest saved version's).
  const currentJudge =
    result?.current?.judge_model ??
    plan?.current?.judge_model ??
    report.judge_model;
  const base = {
    source_key: report.source_key,
    source_template_id: templateId,
    draft_share: Number(share),
    seed,
  };
  const edits = useMemo(
    () =>
      (plan?.candidates ?? [])
        .filter((c, i) => ticked.has(i))
        .map((c) => ({
          kind: c.kind,
          clause: c.clause,
          from: c.from,
          source: c.source,
        })),
    [plan, ticked],
  );

  const makeCopy = async () => {
    setCopyError(null);
    try {
      const copy = await editableCopyOf({
        templateName,
        outputType: report.source?.output_type,
        model: report.judge_model,
      });
      setTarget({ id: copy.id, name: copy.name });
      // The tuned-judge check finds the copy in the agreement answer.
      if (!copy.existed) refreshAgreement();
    } catch (e) {
      setCopyError(e?.message ?? e?.result ?? "Couldn't make the copy");
    }
  };
  const draftPlan = async () => {
    setResult(null);
    setTicked(new Set());
    setPlan(await improve.mutateAsync(base));
  };
  const score = async (instructions) => {
    setConfirm(null);
    const next = await improve.mutateAsync({
      ...base,
      edits,
      ...(instructions ? { instructions } : {}),
    });
    setResult(next);
    setDraftText(next?.draft?.instructions ?? "");
    setSaved(null);
  };
  const save = async () => {
    const draft = result.draft.instructions;
    const { data } = await axios.post(
      endpoints.develop.eval.createEvalVersion(target.id),
      {
        criteria: draft,
        model: currentJudge,
        config_snapshot: {
          rule_prompt: draft,
          model: currentJudge,
          aligned_on: {
            source_key: report.source_key,
            split: result.split,
            before: result.before,
            after: result.after,
            ...(result.current?.version
              ? { from_version: result.current.version }
              : {}),
          },
        },
      },
    );
    const v = data?.result?.version_number;
    setSaved({ version: v });
    queryClient.invalidateQueries({
      queryKey: ["evals", "versions", target.id],
    });
    // The saved version is the current one now (compare, the check).
    refreshAgreement();
    enqueueSnackbar(savedToast(target.name, v), { variant: "success" });
  };

  if (!mode) return null;
  if (!target)
    return (
      <Stack gap={1} data-testid="w7-improve">
        <Button
          variant="outlined"
          size="small"
          onClick={makeCopy}
          sx={{ alignSelf: "flex-start" }}
          data-testid="w7-make-copy"
        >
          Make an editable copy to improve
        </Button>
        <Typography typography="s3" color="text.secondary">
          {templateName} is a built-in rule. The copy {copyNameOf(templateName)}{" "}
          reads “{COPY_INSTRUCTIONS}” with the same judge, and you improve that.
        </Typography>
        {copyError ? <Alert severity="error">{String(copyError)}</Alert> : null}
      </Stack>
    );

  return (
    <Stack gap={1.5} data-testid="w7-improve">
      <Typography typography="s1" fontWeight={600}>
        Improve judge · {target.name}
      </Typography>
      <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
        <Typography typography="s2">Draft from</Typography>
        <TextField
          size="small"
          type="number"
          value={share}
          onChange={(e) => setShare(e.target.value)}
          inputProps={{
            "aria-label": "Draft share %",
            "data-testid": "w7-share",
          }}
          sx={{ width: 80 }}
        />
        <Typography typography="s2">
          % of the labelled rows, check on the rest · seed
        </Typography>
        <TextField
          size="small"
          value={seed}
          onChange={(e) => setSeed(e.target.value)}
          inputProps={{ "aria-label": "Seed", "data-testid": "w7-seed" }}
          sx={{ width: 80 }}
        />
        <Tooltip title={reason ?? ""}>
          <span>
            <Button
              size="small"
              variant="contained"
              disabled={Boolean(reason) || improve.isPending}
              onClick={draftPlan}
              data-testid="w7-draft"
            >
              Draft candidates
            </Button>
          </span>
        </Tooltip>
      </Stack>
      {reason ? (
        <Typography
          typography="s3"
          color="text.secondary"
          data-testid="w7-split-reason"
        >
          {reason}
        </Typography>
      ) : null}
      {plan ? (
        <Stack gap={1}>
          <Typography typography="s2" sx={tabular} data-testid="w7-split-line">
            Drafting from {plan.split.draft_rows.length} rows · checking on{" "}
            {plan.split.check_rows.length} rows the draft never sees
          </Typography>
          {plan.current?.from_saved ? (
            <Typography
              typography="s3"
              color="text.secondary"
              data-testid="w7-improve-current"
            >
              Starts from v{plan.current.version}, the version {target.name}{" "}
              scores with now (judge {plan.current.judge_model}).
            </Typography>
          ) : null}
          <Typography typography="s3" color="text.secondary">
            Rule-based draft from {plan.split.draft_rows.length} labels and{" "}
            {plan.draft_notes} reviewer{" "}
            {plan.draft_notes === 1 ? "note" : "notes"} · edit before publishing
          </Typography>
          <ImproveCandidates
            candidates={plan.candidates}
            ticked={ticked}
            onTick={setTicked}
            edits={edits}
          />
          <Button
            size="small"
            variant="contained"
            disabled={!edits.length || improve.isPending}
            onClick={async () => {
              await refreshPrices();
              setConfirm({});
            }}
            sx={{ alignSelf: "flex-start" }}
            data-testid="w7-score-draft"
          >
            Score the draft
          </Button>
        </Stack>
      ) : null}
      {result?.after ? (
        <Stack gap={0.5} data-testid="w7-improve-result">
          <ImproveResult result={result} source={report.source} />
          <TextField
            multiline
            minRows={3}
            size="small"
            label="Draft instructions"
            value={draftText}
            onChange={(e) => setDraftText(e.target.value)}
            inputProps={{ "data-testid": "w7-draft-text" }}
            sx={{ mt: 1 }}
          />
          {draftText.trim() &&
          draftText.trim() !== result.draft.instructions ? (
            <Button
              size="small"
              variant="outlined"
              onClick={async () => {
                await refreshPrices();
                setConfirm({ instructions: draftText.trim() });
              }}
              sx={{ alignSelf: "flex-start" }}
              data-testid="w7-score-edited"
            >
              Score the edited draft
            </Button>
          ) : null}
          {movedTo ? (
            <Typography
              typography="s3"
              color="text.secondary"
              data-testid="w7-improve-moved"
            >
              v{movedTo} was saved after this draft was scored. Score the draft
              again to build on v{movedTo}.
            </Typography>
          ) : null}
          <Button
            size="small"
            variant="contained"
            disabled={
              Boolean(saved) ||
              Boolean(movedTo) ||
              draftText.trim() !== result.draft.instructions
            }
            onClick={save}
            sx={{ alignSelf: "flex-start" }}
            data-testid="w7-save-version"
          >
            {saved
              ? `Saved v${saved.version}`
              : nextVersion
                ? `Save as v${nextVersion}`
                : "Save as a new version"}
          </Button>
        </Stack>
      ) : null}
      <Dialog open={Boolean(confirm)} onClose={() => setConfirm(null)}>
        <DialogTitle>
          {confirm?.instructions ? "Score the edited draft" : "Score the draft"}
        </DialogTitle>
        <DialogContent>
          <Typography typography="s1" data-testid="w7-improve-confirm">
            {improveConfirm(n, currentJudge, customPrices)}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirm(null)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={() => score(confirm?.instructions)}
            data-testid="w7-improve-go"
          >
            Score
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
};

ImproveJudgePanel.propTypes = {
  templateId: PropTypes.string.isRequired,
  templateName: PropTypes.string,
  mode: PropTypes.oneOf(["improve", "copy", null]),
  editableCopy: PropTypes.object,
  report: PropTypes.object.isRequired,
};

export default ImproveJudgePanel;

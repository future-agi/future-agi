import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { useSnackbar } from "notistack";
import { useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import ModelSelector from "src/sections/evals/components/ModelSelector";
import { DecisionJudgesContext } from "src/sections/common/DecisionJudges/DecisionJudgesContext";
import {
  editableCopyOf,
  useAgreement,
  useCustomPrices,
  useRefreshAgreement,
} from "./alignmentApi";
import { headlineOf, kappaText, openRowsHref, pctText } from "./agreementModel";
import { usdText } from "./judgeCost";
import { checkConfirm, checkedOnOf, testLeaksLeft } from "./trainingSetExport";

const tabular = { fontVariantNumeric: "tabular-nums" };

/**
 * Check a tuned judge (W7-2 B3): the tuned model and the current judge score
 * the test rows the files hold out, through the one scorer; "Use as this
 * eval's judge" only after the check ran for that model. The check runs on
 * the definition the tuned judge is saved on: this eval's current version,
 * or, for a built-in rule, its editable copy's (on the built-in's labels),
 * so the saved version's checked_on describes that version. `planKey`
 * changes when the files change (Prepare, Leave it out): the check is then
 * owed again.
 */
const TunedJudgeCheck = ({
  templateId,
  templateName,
  mode,
  editableCopy,
  report,
  shares,
  base,
  excluded,
  testN,
  plan,
  planKey,
}) => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const refreshAgreement = useRefreshAgreement();
  const { enqueueSnackbar } = useSnackbar();
  const { customPrices, refresh: refreshPrices } = useCustomPrices();
  const [model, setModel] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [checkModel, setCheckModel] = useState(null);
  const [saved, setSaved] = useState(null);
  const [saveError, setSaveError] = useState(null);
  const [copyError, setCopyError] = useState(null);
  useEffect(() => {
    setCheckModel(null);
    setSaved(null);
  }, [planKey]);
  // Where the tuned judge is checked and saved: this eval, or the copy.
  const checkOn =
    mode === "improve"
      ? { id: templateId, name: templateName, judge: report.judge_model }
      : editableCopy
        ? {
            id: editableCopy.id,
            name: editableCopy.name,
            judge: editableCopy.judge_model,
          }
        : null;
  const checked = useAgreement(
    checkOn?.id,
    {
      ...base,
      // An editable copy is checked on the built-in's labels.
      source_template_id: checkOn?.id !== templateId ? templateId : null,
      split: "test",
      compare_judge_model: checkModel,
      // Test rows the file leaves out are left out of the check too.
      exclude_rows: excluded.length ? excluded.join(",") : null,
    },
    { enabled: Boolean(checkModel && checkOn) },
  );
  const cmp = checkModel ? checked.data?.report?.compare ?? null : null;
  const checkedNow = Boolean(cmp && cmp.judge_model === checkModel);
  const leaks = testLeaksLeft(plan);

  const makeCopy = async () => {
    setCopyError(null);
    try {
      // The same copy Improve judge makes (reused once made), with the
      // current judge; the agreement answer then names it.
      await editableCopyOf({
        templateName,
        outputType: report.source?.output_type,
        model: report.judge_model,
      });
      refreshAgreement();
    } catch (e) {
      setCopyError(e?.message ?? e?.result ?? "Couldn't make the copy");
    }
  };

  const useAsJudge = async () => {
    setSaveError(null);
    const checkedOn = checkedOnOf(checked.data.report, shares);
    // The version goes where the check ran (a built-in rule is never
    // versioned: its editable copy).
    const target = { id: checkOn.id, name: checkOn.name };
    try {
      const { data } = await axios.post(
        endpoints.develop.eval.createEvalVersion(target.id),
        {
          model: checkModel,
          config_snapshot: { model: checkModel, checked_on: checkedOn },
        },
      );
      const v = data?.result?.version_number;
      setSaved({ ...target, version: v });
      queryClient.invalidateQueries({ queryKey: ["evals", "versions"] });
      // The saved version is the current one now.
      refreshAgreement();
      enqueueSnackbar(
        `${target.name} v${v} saved with judge ${checkModel} (checked on ${checkedOn.n} test rows)`,
        { variant: "success" },
      );
    } catch (e) {
      setSaveError(e?.message ?? e?.result ?? "Couldn't save the version");
    }
  };

  return (
    <>
      <Stack gap={1} data-testid="w7-tuned-check">
        <Stack direction="row" gap={1.5} alignItems="center" flexWrap="wrap">
          <Typography typography="s1" fontWeight={600}>
            Check a tuned judge
          </Typography>
          <Box sx={{ minWidth: 240 }}>
            <DecisionJudgesContext.Provider value>
              <ModelSelector
                model={model}
                onModelChange={(m) =>
                  setModel(typeof m === "string" ? m : m?.value ?? "")
                }
                showMode={false}
                showPlus={false}
                hideDatasetContextToggle
              />
            </DecisionJudgesContext.Provider>
          </Box>
          <Button
            size="small"
            variant="contained"
            disabled={!model || !testN || !checkOn || checked.isFetching}
            onClick={async () => {
              await refreshPrices();
              setConfirm(true);
            }}
            data-testid="w7-tuned-run"
          >
            Check on test rows
          </Button>
        </Stack>
        {checkOn ? (
          checkOn.id !== templateId ? (
            <Typography typography="s3" color="text.secondary">
              {templateName} is a built-in rule: the check runs on its editable
              copy {checkOn.name} (its current version), on {templateName}
              &apos;s labels, and the tuned judge is saved there.
            </Typography>
          ) : null
        ) : (
          <Stack gap={0.5} alignItems="flex-start">
            <Typography typography="s3" color="text.secondary">
              {templateName} is a built-in rule and is never versioned. A tuned
              judge is checked on, and saved to, its editable copy.
            </Typography>
            <Button
              size="small"
              variant="outlined"
              onClick={makeCopy}
              data-testid="w7-train-make-copy"
            >
              Make an editable copy to check on
            </Button>
            {copyError ? (
              <Alert severity="error">{String(copyError)}</Alert>
            ) : null}
          </Stack>
        )}
        {cmp ? (
          <Stack gap={0.5} data-testid="w7-tuned-result">
            <Typography typography="s2" sx={tabular}>
              {cmp.judge_model} vs reviewers: {headlineOf(cmp)}
            </Typography>
            <Typography typography="s2" sx={tabular}>
              {cmp.current?.judge_model ?? "Current judge"} (current) vs
              reviewers: {headlineOf(cmp.current)}
            </Typography>
            <Stack direction="row" gap={1} alignItems="center">
              <Typography typography="s2" sx={tabular}>
                Judge vs judge: {pctText(cmp.judge_vs_judge?.agreement)} agree ·
                κ {kappaText(cmp.judge_vs_judge?.kappa)} ·{" "}
                {cmp.judge_vs_judge?.differ_rows?.length ?? 0} rows differ
              </Typography>
              {openRowsHref(
                report.source,
                cmp.judge_vs_judge?.differ_rows,
                `${cmp.current?.judge_model} ≠ ${cmp.judge_model}`,
              ) ? (
                <Button
                  size="small"
                  onClick={() =>
                    navigate(
                      openRowsHref(
                        report.source,
                        cmp.judge_vs_judge.differ_rows,
                        `${cmp.current?.judge_model} ≠ ${cmp.judge_model}`,
                      ),
                    )
                  }
                >
                  Open rows
                </Button>
              ) : null}
            </Stack>
            <Typography typography="s3" color="text.secondary" sx={tabular}>
              Both judges scored the same {cmp.n} test rows ({cmp.judge_calls}{" "}
              judge calls) through the same scorer
              {leaks
                ? `; ${leaks} of them ${leaks === 1 ? "has" : "have"} the same input as a train row`
                : ", none of them in the training file"}
              . Billed as a run bills them: {cmp.judge_model}{" "}
              {usdText(cmp.scored_usd?.other)} at its own prices,{" "}
              {cmp.current?.judge_model ?? "the current judge"}{" "}
              {usdText(cmp.scored_usd?.current)}. Nothing was saved on a run.
            </Typography>
          </Stack>
        ) : null}
        <Tooltip
          title={checkedNow ? "" : "Check this model on the test rows first"}
        >
          <span>
            <Button
              size="small"
              variant="outlined"
              disabled={!checkedNow}
              onClick={useAsJudge}
              data-testid="w7-tuned-use"
            >
              {mode === "improve"
                ? "Use as this eval's judge"
                : "Use as the judge of an editable copy"}
            </Button>
          </span>
        </Tooltip>
        {saved ? (
          <Typography typography="s2" data-testid="w7-tuned-saved">
            {saved.name} v{saved.version} judges with {checkModel}.
          </Typography>
        ) : null}
        {saveError ? <Alert severity="error">{String(saveError)}</Alert> : null}
      </Stack>
      <Dialog open={confirm} onClose={() => setConfirm(false)}>
        <DialogTitle>Check a tuned judge</DialogTitle>
        <DialogContent>
          <Typography typography="s1" data-testid="w7-tuned-confirm">
            {model
              ? checkConfirm(testN, model, {
                  current: checkOn?.judge ?? null,
                  customPrices,
                })
              : ""}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirm(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={() => {
              setConfirm(false);
              setSaved(null);
              setCheckModel(model);
            }}
            data-testid="w7-tuned-confirm-go"
          >
            Score
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

TunedJudgeCheck.propTypes = {
  templateId: PropTypes.string.isRequired,
  templateName: PropTypes.string,
  mode: PropTypes.string,
  editableCopy: PropTypes.object,
  report: PropTypes.object.isRequired,
  shares: PropTypes.object.isRequired,
  base: PropTypes.object.isRequired,
  excluded: PropTypes.array.isRequired,
  testN: PropTypes.number.isRequired,
  plan: PropTypes.object.isRequired,
  planKey: PropTypes.string.isRequired,
};

export default TunedJudgeCheck;

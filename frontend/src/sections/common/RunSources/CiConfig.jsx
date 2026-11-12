// Level 3 · CI (S3, options-truth L1 G2, L3 G6). One GitHub Actions file
// per run, `.github/workflows/fi-experiment-<run label>.yml`: checkout, set
// up the language, install, then the runner with this run's id and limits,
// gated by --fail-under. Trigger and pass-rate gate are real settings:
// they change the file and reach the run config as `slot.ci`. The panel
// lists what to commit (the workflow and the files it runs, each with a
// download), the repository secrets and the FI_RUN_ID value. After the run
// is added it waits for the CI job.
import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import {
  Button,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import ScriptBlock, { downloadText } from "./ScriptBlock";
import RecipeRow from "./recipe/RecipeRow";
import {
  CI_THRESHOLD,
  CI_TRIGGERS,
  ciCommitFiles,
  ciOffer,
  ciSecretCommands,
  ciWorkflow,
  ciWorkflowPath,
} from "./snippets/ci";
import useScriptIds from "./recipe/useScriptIds";

/** The CI slot value with its defaults (pure). */
export function ciValueOf(v = {}) {
  const t = Number(v?.threshold);
  return {
    provider: "github",
    trigger: v?.trigger ?? "pull_request",
    threshold: Number.isFinite(t) && t >= 0 && t <= 1 ? t : CI_THRESHOLD,
  };
}

export default function CiConfig({
  entry,
  dataset,
  ids,
  value,
  onChange,
  limits,
  models,
  depth,
  recipe,
  flags,
}) {
  const scriptIds = useScriptIds(dataset, ids);
  const ci = ciValueOf(value?.ci ?? value);
  const emit = (patch) => onChange?.({ ci: { ...ci, ...patch } });
  // the defaults reach the draft too (the run's config names its CI file)
  const sent = useRef(false);
  useEffect(() => {
    if (sent.current || value?.ci) return;
    sent.current = true;
    onChange?.({ ci });
  }, [value?.ci, onChange, ci]);
  const modelList = (models ?? []).map((m) => m?.name ?? m?.id ?? m);
  const offer = ciOffer(entry);
  const runIds = { ...scriptIds, runId: ids?.runId ?? scriptIds?.runId };
  const path = ciWorkflowPath(runIds.runLabel ?? ids?.runLabel);
  const yaml = ciWorkflow({
    entry,
    ids: runIds,
    ci,
    limits,
    models: modelList,
    depth,
    recipe,
    flags: flags ?? ids?.run,
  });
  // the files the job runs (the workflow calls them), to commit with it
  const commit = ciCommitFiles({
    entry,
    ids: { ...runIds, run: flags ?? ids?.run },
    recipe,
  });
  const secretLines = ciSecretCommands({
    entry,
    models: modelList,
    recipe,
    params: (flags ?? ids?.run)?.params,
    runId: runIds.runId,
  });
  if (!offer.ok)
    return (
      <Stack spacing={1} data-testid="ci-config">
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="ci-not-offered"
        >
          {offer.reason}
        </Typography>
      </Stack>
    );
  return (
    <Stack spacing={1} data-testid="ci-config">
      <Stack direction="row" alignItems="center" gap={1}>
        <Iconify
          icon="mdi:source-branch-check"
          width={16}
          sx={{ color: "text.secondary" }}
        />
        <Typography
          typography="s2"
          fontWeight="fontWeightMedium"
          sx={{ fontFamily: "monospace" }}
        >
          {path}
        </Typography>
      </Stack>
      <RecipeRow label="Runs on" hint="When GitHub starts the job">
        <Select
          size="small"
          value={ci.trigger}
          onChange={(e) => emit({ trigger: e.target.value })}
          sx={{ height: 30, typography: "s2", minWidth: 180 }}
          inputProps={{ "aria-label": "CI trigger" }}
          data-testid="ci-trigger"
        >
          {CI_TRIGGERS.map((t) => (
            <MenuItem key={t.id} value={t.id} sx={{ typography: "s2" }}>
              {t.label}
            </MenuItem>
          ))}
        </Select>
      </RecipeRow>
      {entry?.kind === "voice" ? null : (
        <RecipeRow
          label="Fail below"
          hint="The check fails when the pass rate is lower (--fail-under)"
        >
          <TextField
            size="small"
            type="number"
            value={ci.threshold}
            onChange={(e) => emit({ threshold: Number(e.target.value) })}
            inputProps={{
              min: 0,
              max: 1,
              step: 0.05,
              "aria-label": "Fail below pass rate",
              "data-testid": "ci-threshold",
            }}
            sx={{ width: 100, "& input": { py: 0.5, typography: "s2" } }}
          />
        </RecipeRow>
      )}
      <ScriptBlock
        text={yaml}
        language="yaml"
        copyTooltip={`Copy ${path}`}
        testId="ci-yaml"
        fileName={path.split("/").pop()}
      />
      {commit.length ? (
        <RecipeRow
          label="Commit these"
          hint={`The job runs them: commit them next to ${path}`}
        >
          <Stack
            direction="row"
            gap={0.75}
            flexWrap="wrap"
            data-testid="ci-commit-files"
          >
            {commit.map((f) => (
              <Button
                key={f.fileName}
                size="small"
                variant="outlined"
                startIcon={<Iconify icon="mdi:download" width={14} />}
                onClick={() => downloadText(f.fileName, f.text)}
                data-testid="ci-commit-download"
                sx={{ typography: "s2" }}
              >
                {`Download ${f.fileName}`}
              </Button>
            ))}
          </Stack>
        </RecipeRow>
      ) : null}
      {/* L3 G6 (fix 6): the one key row in CI mode. The job reads GitHub
          repository secrets (its env lists them), never Workspace secrets,
          so the model key is set here with gh, next to FI_RUN_ID. */}
      <RecipeRow
        label="Secrets and run id"
        hint="Repository secrets the job reads (the model key too), and this run's id"
      >
        <ScriptBlock
          text={secretLines}
          language="bash"
          copyTooltip="Copy the gh commands"
          testId="ci-secret-commands"
          dense
        />
      </RecipeRow>
      <Typography typography="s2" color="text.secondary">
        After you add the run, it waits for the CI job; each row lands as the
        job uploads it.
      </Typography>
    </Stack>
  );
}

CiConfig.propTypes = {
  entry: PropTypes.object,
  mode: PropTypes.string,
  value: PropTypes.object,
  onChange: PropTypes.func,
  dataset: PropTypes.object,
  ids: PropTypes.shape({
    datasetId: PropTypes.string,
    runLabel: PropTypes.string,
    runId: PropTypes.string,
    token: PropTypes.string,
    run: PropTypes.object,
  }),
  limits: PropTypes.object,
  models: PropTypes.array,
  depth: PropTypes.string,
  /** A harness's recipe: its install line, Node setup and model key. */
  recipe: PropTypes.object,
  /** The form's command flags (runFlagsOf); defaults to ids.run. */
  flags: PropTypes.object,
  onEstimate: PropTypes.func,
  onTryResult: PropTypes.func,
  /** Additive (Ra): the Rerun dialog renders the recipe compactly. */
  compact: PropTypes.bool,
};

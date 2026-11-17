import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useQueryClient } from "@tanstack/react-query";
import { LoadingButton } from "@mui/lab";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import CustomModelDropdown from "src/components/custom-model-dropdown/CustomModelDropdown";
import { HttpVariantEditor } from "src/sections/experiment-detail-classic/ClassicRerunAction";
import { useExperimentDetail } from "src/api/develop/runs";
import { usePromptVersions } from "src/api/develop/prompt";
import {
  appendExperimentVariant,
  appendVariantBody,
  isHttpSource,
} from "src/api/develop/experiment-append";
import {
  experimentStatusQueryOptions,
  isExperimentPending,
} from "src/api/develop/experiment-status-refresh";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import notify from "src/sections/common/ux/toast";
import {
  forgetRerunRefusal,
  sendRerunSubmission,
  useRerunStarts,
  useRerunSubmission,
} from "./rerunSubmission";

const draftOf = (source) => ({
  name: `${source.name || "Prompt"} variant`,
  promptId: source.prompt_id,
  versionId: source.prompt_version,
  model: source.model,
  temperature: source.model_params?.temperature ?? "",
  maxTokens: source.model_params?.max_tokens ?? "",
});

function variantBodyOf(original, draft) {
  const body = appendVariantBody(original, draft);
  const variant = body.prompt_config[0];
  variant.model_params = { ...variant.model_params };
  variant.configuration = { ...variant.configuration };
  if (typeof variant.model === "object") {
    variant.model = { ...variant.model, config: { ...variant.model.config } };
  }
  for (const [field, alias, value] of [
    ["temperature", "temperature", draft.temperature],
    ["max_tokens", "maxTokens", draft.maxTokens],
  ]) {
    // Empty fields inherit the saved version, not a copied override.
    for (const settings of [
      variant.model_params,
      variant.configuration,
      variant.model?.config,
    ]) {
      if (settings) {
        delete settings[field];
        delete settings[alias];
      }
    }
    if (value !== "") variant.model_params[field] = Number(value);
  }
  return body;
}

function PromptVariantEditor({ run, source, running, onClose, onStarted }) {
  const experimentId = run.experiment_id;
  const submission = useRerunSubmission(experimentId);
  // A held submission comes back as it was sent; a refusal of this variant
  // keeps its draft. Otherwise the starting settings stay frozen across
  // status refreshes.
  const resumed =
    submission && (submission.body || submission.source?.id === source.id)
      ? submission
      : null;
  const [original] = useState(() => resumed?.source ?? source);
  const [draft, setDraft] = useState(() => resumed?.draft ?? draftOf(source));
  const versions = usePromptVersions(original.prompt_id);
  const sending = submission?.status === "sending";
  const unconfirmed = submission?.status === "unconfirmed";
  const refusal = resumed?.status === "refused" ? resumed.error : null;
  const pendingBody = submission?.body ?? null;
  const published = (versions.data?.pages ?? [])
    .flatMap((page) => page.results ?? [])
    .filter((version) => !version.is_draft);
  if (!published.some((version) => version.id === original.prompt_version)) {
    published.unshift({
      id: original.prompt_version,
      template_version: original.prompt_version_name || "Original version",
    });
  }
  const validTemperature =
    draft.temperature === "" ||
    (Number.isFinite(Number(draft.temperature)) &&
      Number(draft.temperature) >= 0 &&
      Number(draft.temperature) <= 2);
  const validMaxTokens =
    draft.maxTokens === "" ||
    (Number.isSafeInteger(Number(draft.maxTokens)) &&
      Number(draft.maxTokens) > 0);
  const changed =
    draft.model !== original.model ||
    draft.versionId !== original.prompt_version ||
    String(draft.temperature) !==
      String(original.model_params?.temperature ?? "") ||
    String(draft.maxTokens) !== String(original.model_params?.max_tokens ?? "");
  const valid = Boolean(
    changed &&
      draft.name.trim() &&
      draft.model &&
      draft.versionId &&
      validTemperature &&
      validMaxTokens &&
      !versions.isError,
  );
  const locked = Boolean(pendingBody);
  const disabled = sending || (!pendingBody && (running || !valid));
  const set = (patch) => setDraft((value) => ({ ...value, ...patch }));
  const submit = () => {
    if (disabled) return;
    sendRerunSubmission(
      experimentId,
      {
        body: pendingBody ?? variantBodyOf(original, draft),
        source: original,
        draft,
        label: run.label,
      },
      {
        send: (body) => appendExperimentVariant(experimentId, body),
        onStarted,
      },
    );
  };
  return (
    <Stack
      onKeyDown={(event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
          event.preventDefault();
          submit();
        }
      }}
    >
      <DialogContent dividers>
        <Stack spacing={2}>
          <Typography color="text.secondary">
            Creates a new variant. Earlier variants and results are kept.
          </Typography>
          {running && !pendingBody && (
            <Alert severity="info">
              Wait for the current run to finish before adding a variant.
            </Alert>
          )}
          <TextField
            label="Variant name"
            value={draft.name}
            disabled={locked}
            onChange={(event) => set({ name: event.target.value })}
          />
          <Typography color="text.secondary">
            {original.prompt_name || "Saved prompt"}
          </Typography>
          <TextField
            select
            label="Published version"
            value={draft.versionId}
            disabled={locked}
            onChange={(event) => set({ versionId: event.target.value })}
          >
            {published.map((version) => (
              <MenuItem key={version.id} value={version.id}>
                {version.template_version}
              </MenuItem>
            ))}
          </TextField>
          {versions.hasNextPage && (
            <Button
              disabled={locked || versions.isFetchingNextPage}
              onClick={() => versions.fetchNextPage()}
            >
              Load more versions
            </Button>
          )}
          {versions.isError && (
            <Alert
              severity="error"
              action={<Button onClick={() => versions.refetch()}>Retry</Button>}
            >
              Could not load published versions.
            </Alert>
          )}
          <CustomModelDropdown
            label="Model"
            value={draft.model}
            modelDetail={{ model_name: draft.model }}
            disabledClick={locked}
            extraParams={{ model_type: "llm" }}
            onChange={(event) => set({ model: event.target.value.model_name })}
          />
          <Stack direction="row" gap={2}>
            <TextField
              label="Temperature"
              type="number"
              value={draft.temperature}
              disabled={locked}
              error={!validTemperature}
              inputProps={{ min: 0, max: 2, step: 0.1 }}
              onChange={(event) => set({ temperature: event.target.value })}
            />
            <TextField
              label="Max tokens"
              type="number"
              value={draft.maxTokens}
              disabled={locked}
              error={!validMaxTokens}
              inputProps={{ min: 1, step: 1 }}
              onChange={(event) => set({ maxTokens: event.target.value })}
            />
          </Stack>
          <Typography color="text.secondary">
            Empty settings use the saved prompt defaults. A different model
            keeps the temperature and max tokens shown here.
          </Typography>
          <Typography color="text.secondary">
            This re-run uses every row, one try each.
          </Typography>
          {unconfirmed && (
            <Alert severity="warning">
              The server did not confirm that this variant started. Retry this
              identical submission to confirm whether execution started.
            </Alert>
          )}
          {refusal && (
            <Alert severity="error">
              {getRequestErrorMessage(
                refusal,
                "Could not start the new variant.",
              )}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={sending}>
          {pendingBody ? "Close" : "Cancel"}
        </Button>
        <LoadingButton
          variant="contained"
          loading={sending}
          disabled={disabled}
          onClick={submit}
          data-testid="rerun-run"
        >
          {unconfirmed ? "Retry submission" : "Run new variant"}
        </LoadingButton>
      </DialogActions>
    </Stack>
  );
}

export default function PersistedRerunWithChangesDialog({
  open,
  onClose,
  run,
  initialScope = "all",
  initialRowIds = [],
}) {
  const experimentId = run?.experiment_id;
  const submission = useRerunSubmission(experimentId);
  const held = submission?.body ? submission : null;
  const sending = submission?.status === "sending";
  const detail = useExperimentDetail(experimentId, {
    ...experimentStatusQueryOptions,
    enabled: open && Boolean(experimentId),
  });
  const experiment = detail.data;
  const found = (experiment?.prompt_configs ?? []).find(
    (config) => config.id === run?.prompt_config_id,
  );
  const supported =
    experiment?.experiment_type === "llm" &&
    !["http", "code", "traces", "upload"].includes(run?.source) &&
    run?.task_kind !== "external" &&
    !experiment?.http_configs?.length &&
    found?.prompt_id &&
    found?.prompt_version;
  const httpRun = run?.source === "http" && run?.task_kind === "external";
  const httpSource =
    experimentId &&
    httpRun &&
    run.run_id &&
    run.native_config_id &&
    experiment?.experiment_type === "llm"
      ? (experiment.http_configs ?? []).find(
          (config) =>
            config.id === run.native_config_id && isHttpSource(config),
        )
      : null;
  const running =
    isExperimentPending(experiment?.status) ||
    (experiment?.runs ?? []).some((item) => isExperimentPending(item.status));
  // A held submission is shown from what it sent, so neither a failed
  // background read nor an opening from another run can hide its retry.
  const source =
    held?.source ??
    (!detail.isError ? httpSource || (supported && found) : null);
  const Editor = isHttpSource(source) ? HttpVariantEditor : PromptVariantEditor;
  const close = () => {
    forgetRerunRefusal(experimentId);
    onClose();
  };
  const queryClient = useQueryClient();
  const datasetId = run?.dataset_id;
  // Runs even when the dialog is already gone.
  const onStarted = (detail) => {
    // The append response is experiment detail, not a prototype child
    // run. Its optional Results projection may be unavailable after
    // admission.
    queryClient.setQueryData(["experiment", experimentId], (previous) =>
      previous?.data?.result && detail?.status
        ? {
            ...previous,
            data: {
              ...previous.data,
              result: { ...previous.data.result, status: detail.status },
            },
          }
        : previous,
    );
    queryClient.invalidateQueries({
      queryKey: ["experiment", experimentId],
    });
    queryClient.invalidateQueries({
      queryKey: ["develop", "dataset-runs", datasetId],
    });
    queryClient.invalidateQueries({
      queryKey: ["develop", "run-results", datasetId],
    });
    queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
    notify("Started a new variant. Earlier variants and results are kept.", {
      variant: "success",
    });
  };
  // Close once the server confirms a start for this experiment, whichever
  // opening of the dialog sent it. Starts counted before this experiment
  // was shown here never close it.
  const starts = useRerunStarts(experimentId);
  const seenStarts = useRef({ experimentId, starts });
  useEffect(() => {
    const seen = seenStarts.current;
    if (seen.experimentId !== experimentId) {
      seenStarts.current = { experimentId, starts };
    } else if (starts > seen.starts) {
      seenStarts.current = { experimentId, starts };
      onClose();
    }
  }, [experimentId, starts, onClose]);
  return (
    <Dialog
      open={open}
      // While the request is in flight Escape and the backdrop wait for its
      // answer, which is kept outside the dialog either way.
      onClose={() => {
        if (!sending) close();
      }}
      fullWidth
      maxWidth="sm"
      data-testid="rerun-dialog"
    >
      <DialogTitle>{`Rerun ${held?.label || run?.label || "run"} with changes`}</DialogTitle>
      {source ? (
        <Editor
          key={`${run.run_id}:${source.id}`}
          run={run}
          source={source}
          running={running}
          onClose={close}
          onStarted={onStarted}
          experimentId={experimentId}
          datasetId={datasetId}
          selection={
            initialScope === "all"
              ? null
              : {
                  parent_run_id: run.run_id,
                  scope: initialScope,
                  ...(initialScope === "rows"
                    ? { example_ids: initialRowIds }
                    : {}),
                }
          }
          controller={{
            submission:
              submission?.source?.id === source.id ? submission : null,
            running,
            submit: ({ body, key }, draft) =>
              sendRerunSubmission(
                experimentId,
                {
                  body: { requestBody: body, key },
                  source,
                  draft,
                  label: run.label,
                },
                {
                  send: ({ requestBody, key }) =>
                    appendExperimentVariant(experimentId, requestBody, {
                      idempotencyKey: key,
                    }),
                  onStarted,
                },
              ),
          }}
        />
      ) : (
        <>
          <DialogContent>
            <Alert severity={detail.isLoading ? "info" : "warning"}>
              {detail.isLoading
                ? "Loading the saved variant…"
                : detail.isError
                  ? "Could not read the experiment. Retry the read before starting a variant."
                  : httpRun
                    ? "The saved HTTP variant could not be found for this run. An exact HTTP source binding is required; no request was sent."
                    : "The saved prompt variant could not be found for this run. Only saved prompt variants can be appended here; no request was sent."}
            </Alert>
            {detail.isError && (
              <Button onClick={() => detail.refetch()}>Retry</Button>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={close}>Close</Button>
          </DialogActions>
        </>
      )}
    </Dialog>
  );
}

PersistedRerunWithChangesDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  run: PropTypes.object,
  initialScope: PropTypes.string,
  initialRowIds: PropTypes.array,
};
PromptVariantEditor.propTypes = {
  run: PropTypes.object.isRequired,
  source: PropTypes.object.isRequired,
  running: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  onStarted: PropTypes.func.isRequired,
};

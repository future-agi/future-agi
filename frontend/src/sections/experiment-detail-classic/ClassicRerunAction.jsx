import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Autocomplete,
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
import { LoadingButton } from "@mui/lab";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import CustomModelDropdown from "src/components/custom-model-dropdown/CustomModelDropdown";
import { useExperimentDetail } from "src/api/develop/runs";
import {
  experimentStatusQueryOptions,
  isExperimentPending,
} from "src/api/develop/experiment-status-refresh";
import { usePromptExecutions, usePromptVersions } from "src/api/develop/prompt";
import {
  appendExperimentVariant,
  appendHttpVariantBody,
  appendVariantBody,
  appendVariantSupported,
  httpVariantChanged,
  httpVariantDraftOf,
  httpVariantUrlProblem,
  isHttpSource,
} from "src/api/develop/experiment-append";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import {
  HTTP_RESEND_COPY,
  httpRefusalMessage,
  httpVariantCountLine,
  isUnknownRequestOutcome,
  newHttpActionKey,
} from "src/sections/common/Runs/httpResendCopy";
import { readExperimentRowCount } from "./ClassicHttpRetry";

const HTTP_METHODS = ["POST", "PUT", "GET"];

/**
 * Re-run with changes for an HTTP source: the endpoint settings of one new
 * variant, which sends one request for each row of the experiment (the
 * editor says how many). Headers and the variable mapping travel unchanged
 * from the original. An unknown outcome is a warning, and Check again
 * re-sends only the identical submission, which confirms whether the first
 * one started; it never appends a second variant. An experiment with HTTP
 * runs offers only this editor.
 */
function HttpVariantEditor({
  experimentId,
  source,
  onClose,
  onSaved,
  onLockChange,
}) {
  const [draft, setDraft] = useState(() => httpVariantDraftOf(source));
  // {body, key}: one submission; its recovery repeats both exactly.
  const [pending, setPending] = useState(null);
  const pendingBody = pending?.body ?? null;
  const mutation = useMutation({
    mutationFn: ({ body, key }) =>
      appendExperimentVariant(experimentId, body, { idempotencyKey: key }),
    retry: false,
    meta: { errorHandled: true },
    onSuccess: onSaved,
    onError: (error) => {
      // A 4xx answer did not admit work: let the user change the draft.
      if (!isUnknownRequestOutcome(error)) setPending(null);
    },
  });
  // onError clears the submission on any 4xx, so a failed submission that is
  // still pending is an unknown outcome: it may have started.
  const unconfirmed = Boolean(pendingBody) && mutation.isError;
  const rowCount = useQuery({
    queryKey: ["experiment", experimentId, "httpVariantRowCount"],
    queryFn: () => readExperimentRowCount(experimentId),
    retry: false,
  });
  const original = httpVariantDraftOf(source);
  const get = draft.method === "GET";
  const urlProblem = httpVariantUrlProblem(draft.url);
  const sameEndpoint =
    draft.url.trim() === original.url.trim() && draft.method === original.method;
  const valid =
    httpVariantChanged(source, draft) && draft.name.trim() && !urlProblem;
  const locked = mutation.isPending || Boolean(pendingBody);
  useEffect(() => {
    onLockChange(locked);
  }, [locked, onLockChange]);
  const methods = HTTP_METHODS.includes(original.method)
    ? HTTP_METHODS
    : [...HTTP_METHODS, original.method];
  const set = (patch) => setDraft((now) => ({ ...now, ...patch }));
  const submit = () => {
    const next = pending ?? {
      body: appendHttpVariantBody(source, draft),
      key: newHttpActionKey(),
    };
    setPending(next);
    mutation.mutate(next);
  };
  return (
    <>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <Typography color="text.secondary">
            Creates one new HTTP variant on the same snapshot. Earlier variants
            and their results are kept.
          </Typography>
          <Typography color="text.secondary">
            {httpVariantCountLine(rowCount.data)}
          </Typography>
          <TextField
            label="Variant name"
            value={draft.name}
            disabled={locked}
            onChange={(e) => set({ name: e.target.value })}
          />
          <TextField
            label="URL"
            value={draft.url}
            disabled={locked}
            error={Boolean(urlProblem)}
            helperText={urlProblem}
            onChange={(e) => set({ url: e.target.value })}
          />
          <TextField
            select
            label="Method"
            value={draft.method}
            disabled={locked}
            onChange={(e) => set({ method: e.target.value })}
          >
            {methods.map((method) => (
              <MenuItem key={method} value={method}>
                {method}
              </MenuItem>
            ))}
          </TextField>
          {!get && (
            <TextField
              label="Body template"
              value={draft.bodyTemplate}
              disabled={locked}
              multiline
              minRows={4}
              onChange={(e) => set({ bodyTemplate: e.target.value })}
            />
          )}
          <TextField
            label="Output path"
            value={draft.outputPath}
            disabled={locked}
            onChange={(e) => set({ outputPath: e.target.value })}
          />
          <Typography color="text.secondary">
            {HTTP_RESEND_COPY.appendCopied}
          </Typography>
          {sameEndpoint && (
            <Alert severity="warning">
              {HTTP_RESEND_COPY.appendSameEndpoint}
            </Alert>
          )}
          {mutation.isError && (
            <Alert severity={unconfirmed ? "warning" : "error"}>
              {unconfirmed
                ? HTTP_RESEND_COPY.checkAgainHelp
                : httpRefusalMessage(
                    mutation.error,
                    "Could not start the new variant.",
                  )}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button disabled={mutation.isPending} onClick={onClose}>
          Close
        </Button>
        <LoadingButton
          loading={mutation.isPending}
          disabled={!pendingBody && !valid}
          variant="contained"
          onClick={submit}
        >
          {unconfirmed ? HTTP_RESEND_COPY.checkAgain : "Run new variant"}
        </LoadingButton>
      </DialogActions>
    </>
  );
}

function VariantEditor({
  experimentId,
  source,
  onClose,
  onSaved,
  onLockChange,
}) {
  const [draft, setDraft] = useState({
    name: `${source.name} variant`,
    promptId: source.prompt_id,
    versionId: source.prompt_version,
    model: source.model,
  });
  const [promptSearch, setPromptSearch] = useState("");
  const [selectedPrompt, setSelectedPrompt] = useState({
    id: source.prompt_id,
    name: source.prompt_name,
  });
  const prompts = usePromptExecutions(true, promptSearch, "chat");
  const versions = usePromptVersions(draft.promptId);
  const [pendingBody, setPendingBody] = useState(null);
  const mutation = useMutation({
    mutationFn: (body) => appendExperimentVariant(experimentId, body),
    retry: false,
    onSuccess: onSaved,
    onError: (error) => {
      // A definitive client error did not admit work; an unknown outcome must
      // retry the identical request instead of appending a second variant.
      const status = error?.response?.status;
      if (status >= 400 && status < 500) setPendingBody(null);
    },
  });
  const promptOptions =
    prompts.data?.pages.flatMap((page) => page.results) ?? [];
  if (selectedPrompt && !promptOptions.some((p) => p.id === selectedPrompt.id))
    promptOptions.unshift(selectedPrompt);
  const savedVersions = (
    versions.data?.pages.flatMap((page) => page.results) ?? []
  ).filter((v) => !v.is_draft);
  if (
    draft.promptId === source.prompt_id &&
    !savedVersions.some((v) => v.id === source.prompt_version)
  ) {
    savedVersions.unshift({
      id: source.prompt_version,
      template_version: source.prompt_version_name,
    });
  }
  const changed =
    draft.model !== source.model ||
    draft.promptId !== source.prompt_id ||
    draft.versionId !== source.prompt_version;
  const valid =
    changed &&
    draft.name.trim() &&
    draft.model &&
    draft.promptId &&
    draft.versionId;
  const locked = mutation.isPending || Boolean(pendingBody);
  useEffect(() => {
    onLockChange(locked);
  }, [locked, onLockChange]);
  const submit = () => {
    const body = pendingBody ?? appendVariantBody(source, draft);
    setPendingBody(body);
    mutation.mutate(body);
  };
  return (
    <>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <Typography color="text.secondary">
            Creates one new variant on the same snapshot. Earlier variants and
            their results are kept.
          </Typography>
          <TextField
            label="Variant name"
            value={draft.name}
            disabled={locked}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
          <CustomModelDropdown
            label="Model"
            value={draft.model}
            modelDetail={{ model_name: draft.model }}
            disabledClick={locked}
            extraParams={{ model_type: "llm" }}
            onChange={(event) =>
              setDraft({ ...draft, model: event.target.value.model_name })
            }
          />
          <Autocomplete
            options={promptOptions}
            value={selectedPrompt}
            disabled={locked}
            loading={prompts.isLoading}
            getOptionLabel={(option) => option.name ?? "Untitled prompt"}
            isOptionEqualToValue={(a, b) => a.id === b.id}
            filterOptions={(options) => options}
            onInputChange={(_, text, reason) => {
              if (reason === "input") setPromptSearch(text);
            }}
            onChange={(_, prompt) => {
              setSelectedPrompt(prompt);
              setDraft({ ...draft, promptId: prompt?.id ?? "", versionId: "" });
            }}
            renderInput={(params) => <TextField {...params} label="Prompt" />}
          />
          {prompts.hasNextPage && (
            <Button
              disabled={locked || prompts.isFetchingNextPage}
              onClick={() => prompts.fetchNextPage()}
            >
              Load more prompts
            </Button>
          )}
          <TextField
            select
            label="Published version"
            value={draft.versionId}
            disabled={locked || !draft.promptId}
            onChange={(e) => setDraft({ ...draft, versionId: e.target.value })}
          >
            {savedVersions.map((version) => (
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
          {(prompts.isError || versions.isError) && (
            <Alert
              severity="error"
              action={
                <Button
                  onClick={() => {
                    prompts.refetch();
                    versions.refetch();
                  }}
                >
                  Retry
                </Button>
              }
            >
              Could not load prompts or versions.
            </Alert>
          )}
          {draft.model !== source.model && (
            <Typography color="text.secondary">
              The new model uses saved prompt defaults. Model-specific settings
              from the original variant are not copied.
            </Typography>
          )}
          {mutation.isError && (
            <Alert severity="error">
              {getRequestErrorMessage(
                mutation.error,
                "Could not start the new variant.",
              )}
              {pendingBody
                ? " Retry this submission to confirm whether execution started."
                : ""}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button disabled={mutation.isPending} onClick={onClose}>
          Close
        </Button>
        <LoadingButton
          loading={mutation.isPending}
          disabled={
            !pendingBody && (!valid || prompts.isError || versions.isError)
          }
          variant="contained"
          onClick={submit}
        >
          {pendingBody && mutation.isError
            ? "Retry submission"
            : "Run new variant"}
        </LoadingButton>
      </DialogActions>
    </>
  );
}

function AvailableRerunAction({ experimentId, onSaved }) {
  const [open, setOpen] = useState(false);
  const [sourceId, setSourceId] = useState("");
  const [locked, setLocked] = useState(false);
  // The grid polls its own row pages; this read follows the execution that
  // gates the action until it reaches a terminal status.
  const detail = useExperimentDetail(
    experimentId,
    experimentStatusQueryOptions,
  );
  const queryClient = useQueryClient();
  // SRC S1b: an experiment with HTTP runs appends HTTP variants only. The
  // server sends every append there through the HTTP path, which refuses a
  // prompt or agent variant, so only the HTTP sources can be picked; its
  // prompt runs stay listed, disabled, with the reason (nothing disappears).
  const httpConfigs = detail.data?.http_configs ?? [];
  const promptRuns = (detail.data?.prompt_configs ?? []).filter(
    (config) => config.prompt_id && config.prompt_version,
  );
  const sources = httpConfigs.length
    ? httpConfigs.filter((config) => config?.id && isHttpSource(config))
    : promptRuns;
  const blocked = httpConfigs.length ? promptRuns : [];
  const source = sources.find((config) => config.id === sourceId) ?? sources[0];
  const Editor = isHttpSource(source) ? HttpVariantEditor : VariantEditor;
  const running = isExperimentPending(detail.data?.status);
  if (detail.data?.experiment_type !== "llm") return null;
  if (!source && !blocked.length) return null;
  if (!source)
    return (
      <Stack direction="row" alignItems="center" gap={1}>
        <Button
          variant="outlined"
          disabled
          aria-describedby="classic-rerun-blocked"
        >
          Re-run with changes
        </Button>
        <Typography
          id="classic-rerun-blocked"
          typography="s3"
          color="text.secondary"
        >
          {HTTP_RESEND_COPY.rerunNothingToCopy}
        </Typography>
      </Stack>
    );
  return (
    <>
      <Button
        variant="outlined"
        disabled={running && !locked}
        onClick={() => setOpen(true)}
      >
        Re-run with changes
      </Button>
      {running && detail.isError && (
        <Button disabled={detail.isFetching} onClick={() => detail.refetch()}>
          Refresh status
        </Button>
      )}
      <Dialog
        open={open}
        keepMounted
        onClose={() => setOpen(false)}
        fullWidth
        maxWidth="sm"
        aria-labelledby="classic-rerun-title"
      >
        <DialogTitle id="classic-rerun-title">Re-run with changes</DialogTitle>
        <DialogContent>
          <TextField
            select
            fullWidth
            disabled={locked}
            label="Original variant"
            value={source.id}
            onChange={(e) => setSourceId(e.target.value)}
            sx={{ mt: 1 }}
          >
            {sources.map((item) => (
              <MenuItem key={item.id} value={item.id}>
                {item.name || (isHttpSource(item) ? "HTTP endpoint" : "")}
              </MenuItem>
            ))}
            {blocked.map((item) => (
              <MenuItem
                key={`blocked-${item.id}`}
                value={`blocked-${item.id}`}
                disabled
              >
                {`${item.name || "Prompt run"}: ${HTTP_RESEND_COPY.rerunPromptBlocked}`}
              </MenuItem>
            ))}
          </TextField>
        </DialogContent>
        {(open || locked) && (
          <Editor
            key={source.id}
            experimentId={experimentId}
            source={source}
            onClose={() => setOpen(false)}
            onLockChange={setLocked}
            onSaved={() => {
              setLocked(false);
              setOpen(false);
              queryClient.invalidateQueries({
                queryKey: ["experiment", experimentId],
              });
              onSaved?.();
            }}
          />
        )}
      </Dialog>
    </>
  );
}

export default function ClassicRerunAction(props) {
  if (!isSurfaceShown("exp.rerun") || !appendVariantSupported()) return null;
  return <AvailableRerunAction {...props} />;
}

ClassicRerunAction.propTypes = AvailableRerunAction.propTypes = {
  experimentId: PropTypes.string.isRequired,
  onSaved: PropTypes.func,
};
VariantEditor.propTypes = HttpVariantEditor.propTypes = {
  experimentId: PropTypes.string.isRequired,
  source: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
  onSaved: PropTypes.func.isRequired,
  onLockChange: PropTypes.func.isRequired,
};

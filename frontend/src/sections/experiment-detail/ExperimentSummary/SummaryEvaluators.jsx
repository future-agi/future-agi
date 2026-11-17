import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { useEvalsList } from "src/sections/evals/hooks/useEvalsList";
import { useEvalVersions } from "src/sections/evals/hooks/useEvalVersions";

const api = () => endpoints.develop.experiment;
const readResult = async (url, signal) =>
  (await axios.get(url, { signal })).data.result;
const message = (error) =>
  error?.response?.data?.message || "The summary request failed. Please retry.";

function SummaryEvaluatorPanel({ experimentId, runIds }) {
  const client = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [search, setSearch] = useState("");
  const [name, setName] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [mapping, setMapping] = useState('{"rows":"input.rows"}');
  const [formError, setFormError] = useState("");
  const key = ["experiment-summary-evaluators", experimentId];
  const bindings = useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      readResult(api().summaryEvaluators(experimentId), signal),
  });
  const templates = useEvalsList({ search, enabled: adding, pageSize: 50 });
  const versions = useEvalVersions(adding ? templateId : null);
  const results = useQueries({
    queries: runIds.map((runId) => ({
      queryKey: ["experiment-summary-scores", experimentId, runId],
      queryFn: ({ signal }) =>
        readResult(api().summaryScores(experimentId, runId), signal),
      refetchInterval: (query) => {
        if (query.state.status === "error") return false;
        const scores = query.state.data?.scores || [];
        if (
          scores.some(
            (score) =>
              ["pending", "running"].includes(score.status) &&
              !score.interrupted,
          )
        ) {
          return 3000;
        }
        // A run can seal after the first, empty read. Wait for its expected
        // results too, but stop after 100 reads if no result is ever scheduled.
        const missing = bindings.data?.bindings?.some(
          (binding) => !scores.some((score) => score.binding_id === binding.id),
        );
        return missing && query.state.dataUpdateCount < 100 ? 3000 : false;
      },
    })),
  });
  const add = useMutation({
    mutationFn: (body) =>
      axios.post(api().summaryEvaluators(experimentId), body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: key });
      setAdding(false);
      setName("");
    },
  });
  const evaluate = useMutation({
    mutationFn: ({ runId, bindingId, retryFailed }) =>
      axios.post(api().summaryScores(experimentId, runId), {
        binding_id: bindingId,
        retry_failed: retryFailed,
      }),
    onSuccess: (_, { runId }) =>
      client.invalidateQueries({
        queryKey: ["experiment-summary-scores", experimentId, runId],
      }),
  });

  const submit = (event) => {
    event.preventDefault();
    setFormError("");
    let parsed;
    try {
      parsed = JSON.parse(mapping);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
        throw new Error("Invalid mapping");
      }
    } catch {
      setFormError("Input mapping must be a JSON object.");
      return;
    }
    add.mutate({
      name,
      template_id: templateId,
      version_id: versionId,
      mapping: parsed,
    });
  };

  return (
    <Stack spacing={2} sx={{ p: 2 }}>
      <Typography variant="h6">Summary evaluators</Typography>
      <Typography
        variant="body2"
        color="text.secondary"
        data-testid="exp.summary-evaluators-scope-line"
      >
        Run saved prompt or code evaluators over every row of a sealed run, not
        just the filtered table.
      </Typography>
      {bindings.isError && (
        <Alert
          severity="error"
          action={
            <Button onClick={() => bindings.refetch()}>Retry read</Button>
          }
        >
          Summary evaluators could not be loaded.
        </Alert>
      )}
      {evaluate.isError && (
        <Alert severity="error">{message(evaluate.error)}</Alert>
      )}
      <Button onClick={() => setAdding(!adding)}>
        {adding ? "Cancel addition" : "Add summary evaluator"}
      </Button>
      {adding && (
        <Stack component="form" spacing={2} onSubmit={submit}>
          <Typography variant="body2">
            Choose a saved prompt or custom-code evaluator from Evaluators. The
            rows parameter contains input, expected, output, status and scores
            for every example/trial. Other mappings may use input.outputs,
            input.expected, input.scores or input.run.
          </Typography>
          <Button
            href="/dashboard/evaluations"
            target="_blank"
            rel="noopener noreferrer"
          >
            Open Evaluators
          </Button>
          <TextField
            label="Summary name"
            value={name}
            required
            onChange={(event) => setName(event.target.value)}
            inputProps={{ maxLength: 255 }}
          />
          <TextField
            label="Search evaluators"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          {templates.isError && (
            <Alert severity="error">
              Evaluator choices could not be loaded.
            </Alert>
          )}
          <TextField
            select
            label="Evaluator"
            value={templateId}
            required
            onChange={(event) => {
              setTemplateId(event.target.value);
              setVersionId("");
            }}
          >
            {(templates.data?.items || [])
              .filter(
                (item) =>
                  ["code", "llm"].includes(item.eval_type) &&
                  item.template_type !== "composite",
              )
              .map((item) => (
                <MenuItem key={item.id} value={item.id}>
                  {item.name}
                </MenuItem>
              ))}
          </TextField>
          {versions.isError && (
            <Alert severity="error">Saved versions could not be loaded.</Alert>
          )}
          <TextField
            select
            label="Pinned version"
            value={versionId}
            required
            disabled={!templateId}
            onChange={(event) => setVersionId(event.target.value)}
          >
            {(versions.data?.versions || []).map((version) => (
              <MenuItem key={version.id} value={version.id}>
                v{version.version_number}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            label="Input mapping (JSON)"
            multiline
            value={mapping}
            onChange={(event) => setMapping(event.target.value)}
          />
          {formError && <Alert severity="error">{formError}</Alert>}
          {add.isError && <Alert severity="error">{message(add.error)}</Alert>}
          <Button
            type="submit"
            disabled={
              add.isPending || !name.trim() || !templateId || !versionId
            }
          >
            Save summary evaluator
          </Button>
        </Stack>
      )}
      {bindings.isLoading && (
        <Typography>Loading summary evaluators…</Typography>
      )}
      {!bindings.isError && bindings.data?.bindings?.length === 0 && (
        <Typography>No summary evaluators configured.</Typography>
      )}
      {!bindings.isError && Boolean(bindings.data?.bindings?.length) && (
        <Box sx={{ overflowX: "auto" }}>
          <Table aria-label="Run summary scores">
            <TableHead>
              <TableRow>
                <TableCell>Evaluator</TableCell>
                {runIds.map((runId) => (
                  <TableCell key={runId}>Run {runId}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {bindings.data.bindings.map((binding) => (
                <TableRow key={binding.id}>
                  <TableCell>
                    {binding.name} · v{binding.version_number}
                  </TableCell>
                  {runIds.map((runId, index) => {
                    const query = results[index];
                    const score = query.data?.scores?.find(
                      (item) => item.binding_id === binding.id,
                    );
                    return (
                      <TableCell key={runId}>
                        {query.isError ? (
                          <Button onClick={() => query.refetch()}>
                            Retry score read
                          </Button>
                        ) : query.isLoading ? (
                          "Loading…"
                        ) : (
                          <Stack spacing={1}>
                            <Typography>
                              {score?.interrupted
                                ? "Interrupted; reconciliation required"
                                : score?.publication_pending
                                  ? "Pending publication"
                                  : score?.status === "completed"
                                    ? String(score.score)
                                    : score?.status || "Not evaluated"}
                            </Typography>
                            {score?.reason && (
                              <Typography>{score.reason}</Typography>
                            )}
                            {score?.error_code && (
                              <Typography>{score.error_code}</Typography>
                            )}
                            {score?.metadata &&
                              Object.keys(score.metadata).length > 0 && (
                                <Box component="details">
                                  <summary>Metadata</summary>
                                  <Box
                                    component="pre"
                                    sx={{ whiteSpace: "pre-wrap" }}
                                  >
                                    {JSON.stringify(score.metadata, null, 2)}
                                  </Box>
                                </Box>
                              )}
                            {!score && (
                              <Button
                                disabled={query.isFetching}
                                onClick={() => query.refetch()}
                              >
                                Refresh scores
                              </Button>
                            )}
                            {(!score ||
                              score.status === "failed" ||
                              score.publication_pending ||
                              score.error_code === "dispatch_failed") && (
                              <Button
                                disabled={evaluate.isPending}
                                onClick={() =>
                                  evaluate.mutate({
                                    runId,
                                    bindingId: binding.id,
                                    retryFailed: score?.status === "failed",
                                  })
                                }
                              >
                                {score?.status === "failed"
                                  ? "Retry failed"
                                  : score
                                    ? "Retry scheduling"
                                    : "Evaluate"}
                              </Button>
                            )}
                          </Stack>
                        )}
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {runIds.length === 0 && (
            <Typography>No runs to evaluate yet.</Typography>
          )}
        </Box>
      )}
    </Stack>
  );
}

const props = {
  experimentId: PropTypes.string.isRequired,
  runIds: PropTypes.arrayOf(PropTypes.string).isRequired,
};
SummaryEvaluatorPanel.propTypes = props;

export default function SummaryEvaluators(properties) {
  if (!isSurfaceShown("exp.summary-evaluators")) return null;
  return (
    <SummaryEvaluatorPanel key={properties.experimentId} {...properties} />
  );
}
SummaryEvaluators.propTypes = props;

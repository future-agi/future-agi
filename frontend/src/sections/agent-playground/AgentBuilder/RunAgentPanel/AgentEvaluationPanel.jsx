import React, { useMemo, useState, useCallback, useEffect } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Chip,
  Divider,
  LinearProgress,
  Stack,
  Typography,
} from "@mui/material";
import LoadingButton from "@mui/lab/LoadingButton";
import EvaluationSelectionDialog from "src/components/run-tests/EvaluationSelectionDialog";
import { resetEvalStore } from "src/sections/evals/store/useEvalStore";
import { useEvaluateExecution } from "src/api/agent-playground/agent-playground";
import {
  buildAgentEvaluationColumns,
  getEvaluatorId,
  hasEvaluatorMappings,
} from "../../utils/evaluationUtils";

const scoreLabel = (score) =>
  typeof score === "number" ? `${Math.round(score * 100)}%` : "NA";

const storageKey = (graphId) => `agent-playground:evaluations:${graphId}`;

const loadSavedEvaluators = (graphId) => {
  if (!graphId) return [];
  try {
    return JSON.parse(window.localStorage.getItem(storageKey(graphId)) || "[]");
  } catch {
    return [];
  }
};

export default function AgentEvaluationPanel({
  graphId,
  executionId,
  executionData,
}) {
  const [open, setOpen] = useState(false);
  const [evaluators, setEvaluators] = useState(() =>
    loadSavedEvaluators(graphId),
  );
  const [summary, setSummary] = useState(
    executionData?.outputPayload?.agentEvaluations?.at?.(-1) ||
      executionData?.output_payload?.agent_evaluations?.at?.(-1) ||
      null,
  );
  const columns = useMemo(
    () => buildAgentEvaluationColumns(executionData),
    [executionData],
  );
  const { mutateAsync, isPending, error } = useEvaluateExecution();

  useEffect(() => {
    setSummary(
      executionData?.outputPayload?.agentEvaluations?.at?.(-1) ||
        executionData?.output_payload?.agent_evaluations?.at?.(-1) ||
        null,
    );
  }, [executionData]);

  useEffect(() => {
    setEvaluators(loadSavedEvaluators(graphId));
  }, [graphId]);

  useEffect(() => {
    if (!graphId) return;
    window.localStorage.setItem(
      storageKey(graphId),
      JSON.stringify(evaluators),
    );
  }, [evaluators, graphId]);

  const handleAddEvaluation = useCallback((nextEvaluation) => {
    setEvaluators((current) => {
      const previousId =
        nextEvaluation?.previousId || nextEvaluation?.previous_id;
      const next = previousId
        ? current.filter((item) => getEvaluatorId(item) !== previousId)
        : current;
      return [...next, nextEvaluation];
    });
    setOpen(false);
    resetEvalStore();
  }, []);

  const handleEvaluate = useCallback(async () => {
    const payload = {
      evaluators,
      threshold: 0.5,
    };
    const response = await mutateAsync({ graphId, executionId, payload });
    setSummary(response.data?.result || null);
  }, [evaluators, executionId, graphId, mutateAsync]);

  const canEvaluate =
    !!graphId && !!executionId && hasEvaluatorMappings(evaluators);

  return (
    <Box sx={{ px: 2, pt: 2 }}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
        <Button
          type="button"
          size="small"
          variant="outlined"
          onClick={() => setOpen(true)}
          disabled={!executionId || columns.length === 0}
        >
          Add Eval
        </Button>
        <LoadingButton
          type="button"
          size="small"
          variant="contained"
          loading={isPending}
          disabled={!canEvaluate}
          onClick={handleEvaluate}
        >
          Evaluate Agent
        </LoadingButton>
        {evaluators.map((item) => (
          <Chip
            key={getEvaluatorId(item) || item.name || item.templateName}
            size="small"
            label={item.name || item.templateName || "Evaluation"}
            onDelete={() =>
              setEvaluators((current) =>
                current.filter(
                  (entry) => getEvaluatorId(entry) !== getEvaluatorId(item),
                ),
              )
            }
          />
        ))}
      </Stack>
      {isPending && <LinearProgress sx={{ mt: 1 }} />}
      {error && (
        <Alert severity="error" sx={{ mt: 1 }}>
          {error?.response?.data?.result || "Failed to evaluate agent"}
        </Alert>
      )}
      {summary && (
        <Stack spacing={1} sx={{ mt: 1 }}>
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            flexWrap="wrap"
          >
            <Chip
              size="small"
              color={summary.passed ? "success" : "error"}
              label={summary.passed ? "Pass" : "Fail"}
            />
            <Typography typography="s2" color="text.secondary">
              Aggregate score {scoreLabel(summary.score)}
            </Typography>
            {summary.history?.length > 0 && (
              <Typography typography="s2" color="text.secondary">
                {summary.history.length} historical run
                {summary.history.length === 1 ? "" : "s"}
              </Typography>
            )}
          </Stack>
          <Stack spacing={0.75}>
            {(summary.results || []).map((result) => (
              <Stack
                key={result.template_id || result.name}
                direction="row"
                spacing={1}
                alignItems="center"
                flexWrap="wrap"
              >
                <Chip
                  size="small"
                  color={result.passed ? "success" : "error"}
                  label={result.passed ? "Pass" : "Fail"}
                />
                <Typography typography="s2" color="text.primary">
                  {result.name || "Evaluation"}
                </Typography>
                <Typography typography="s2" color="text.secondary">
                  {scoreLabel(result.score)}
                </Typography>
              </Stack>
            ))}
          </Stack>
          {summary.history?.length > 1 && (
            <>
              <Divider />
              <Stack direction="row" spacing={0.75} flexWrap="wrap">
                {summary.history.map((item) => (
                  <Chip
                    key={item.execution_id || item.created_at}
                    size="small"
                    variant="outlined"
                    color={item.passed ? "success" : "error"}
                    label={scoreLabel(item.score)}
                  />
                ))}
              </Stack>
            </>
          )}
        </Stack>
      )}
      <EvaluationSelectionDialog
        open={open}
        onClose={() => {
          setOpen(false);
          resetEvalStore();
        }}
        scenarioColumnConfig={columns}
        onAddEvaluation={handleAddEvaluation}
        datasetId={null}
        module="agent-playground"
      />
    </Box>
  );
}

AgentEvaluationPanel.propTypes = {
  graphId: PropTypes.string,
  executionId: PropTypes.string,
  executionData: PropTypes.object,
};

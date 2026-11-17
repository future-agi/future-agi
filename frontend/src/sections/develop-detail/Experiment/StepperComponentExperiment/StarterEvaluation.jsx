import React from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useMutation } from "@tanstack/react-query";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import {
  STARTER_JUDGE,
  ensureStarterJudge,
  starterEvalConfig,
} from "./starterJudge";

/**
 * The empty Evaluations step's one-click judge (JRN-EXP-NO-PROMPT-DEADEND):
 * an experiment needs at least one evaluation, and a first one has none to
 * pick from. `Add Answers the request` makes the judge (or finds the one an
 * earlier first experiment made) and adds it like any picked evaluation.
 */
const StarterEvaluation = ({ model, onAdd }) => {
  const add = useMutation({
    mutationFn: () => ensureStarterJudge(model),
    // The step says why it failed; no second, generic toast.
    meta: { errorHandled: true },
    onSuccess: (templateId) => onAdd(starterEvalConfig({ templateId, model })),
  });
  return (
    <Stack
      alignItems="center"
      spacing={1}
      data-testid="starter-evaluation"
      sx={{ mt: 1.5 }}
    >
      <Typography typography="s2" color="text.secondary" sx={{ maxWidth: 520 }}>
        {`Start with ${STARTER_JUDGE.displayName}: a judge that checks each reply answers what the run was asked. It uses ${model}, the same model as your run.`}
      </Typography>
      <LoadingButton
        size="small"
        variant="contained"
        color="primary"
        loading={add.isPending}
        onClick={() => add.mutate()}
      >
        {`Add ${STARTER_JUDGE.displayName}`}
      </LoadingButton>
      {add.isError ? (
        <Typography typography="s2" color="error.main" role="alert">
          {`Couldn't add ${STARTER_JUDGE.displayName}. ${getRequestErrorMessage(
            add.error,
            "Try again, or use Add evaluation.",
          )}`}
        </Typography>
      ) : null}
    </Stack>
  );
};

StarterEvaluation.propTypes = {
  model: PropTypes.string.isRequired,
  onAdd: PropTypes.func.isRequired,
};

export default StarterEvaluation;

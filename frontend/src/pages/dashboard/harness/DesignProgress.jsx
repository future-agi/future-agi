import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import Iconify from "src/components/iconify";
import { harnessIdempotencyKey } from "src/api/harness/harness";
import { continueHarnessDesign } from "src/api/harness/releases";
import { paths } from "src/routes/paths";

import {
  CONTINUABLE_DESIGN_CODES,
  DESIGN_STEPS,
  designProgress,
  errorMessage,
} from "./harnessShared";

const GLYPHS = {
  done: { icon: "solar:check-circle-bold", color: "accent.pass" },
  active: { icon: "solar:record-circle-bold", color: "accent.info" },
  failed: { icon: "solar:close-circle-bold", color: "accent.fail" },
  pending: { icon: "solar:record-circle-linear", color: "text.secondary" },
};

// A design job (environment-releases.md §7.9): the builder's steps while it runs, or
// Continue for a design that stopped waiting for an answer. Nothing once it's finished.
export default function DesignProgress({ job, conversation, stage, failure }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const done = designProgress(conversation);
  const currentIndex = DESIGN_STEPS.findIndex((step) => !done.has(step.key));
  const design = job?.metadata?.design || {};
  const completed = stage === "completed";
  const failed = stage === "failed" || stage === "canceled";
  const continuable =
    failed && CONTINUABLE_DESIGN_CODES.includes(failure?.code);
  const [continuing, setContinuing] = useState(false);
  const [error, setError] = useState("");
  const [idempotencyKey] = useState(harnessIdempotencyKey);

  const stateOf = (index) => {
    if (completed || done.has(DESIGN_STEPS[index].key)) return "done";
    if (index === currentIndex) {
      if (failure?.code === "design_waiting") return "active";
      return failed ? "failed" : "active";
    }
    return "pending";
  };

  const carryOn = async () => {
    setContinuing(true);
    setError("");
    try {
      const next = await continueHarnessDesign(job.job_id, {
        idempotency_key: idempotencyKey,
      });
      queryClient.invalidateQueries({ queryKey: ["harness-jobs"] });
      navigate(paths.dashboard.simulate.environments.detail(next.job.job_id));
    } catch (requestError) {
      setError(errorMessage(requestError));
      setContinuing(false);
    }
  };

  // A finished design is the environment itself; its Ready pill and tabs say the rest.
  if (completed) return null;

  return (
    <Box sx={{ mb: 2 }} aria-label="Design progress">
      <Typography variant="subtitle2" sx={{ mb: 1 }}>
        {design.template
          ? `Customizing ${design.template.suite?.name || "a template"}`
          : "Designing your environment"}
        {design.resume_from ? " (continued)" : ""}
      </Typography>
      <Stack spacing={0.75}>
        {DESIGN_STEPS.map((step, index) => {
          const state = stateOf(index);
          return (
            <Stack
              key={step.key}
              direction="row"
              spacing={1}
              alignItems="center"
            >
              <Iconify
                icon={GLYPHS[state].icon}
                color={GLYPHS[state].color}
                width={16}
              />
              <Typography
                variant="body2"
                color={state === "pending" ? "text.secondary" : "text.primary"}
                fontWeight={state === "active" ? 600 : 400}
              >
                {step.label}
              </Typography>
            </Stack>
          );
        })}
      </Stack>
      {(design.documents || []).length > 0 && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 1 }}
        >
          Documents: {design.documents.map((one) => one.name).join(", ")}
        </Typography>
      )}
      {continuable && (
        <Stack spacing={1} sx={{ mt: 1.5 }}>
          <Typography variant="body2" color="text.secondary">
            {failure.code === "design_waiting"
              ? "The design is waiting for your answer. Continue it to answer and carry on."
              : "The design ran out of time. Continue it from where it stopped."}
          </Typography>
          <Box>
            <Button
              size="small"
              variant="contained"
              onClick={carryOn}
              disabled={continuing}
              startIcon={
                continuing ? (
                  <CircularProgress size={14} color="inherit" />
                ) : (
                  <Iconify icon="solar:play-linear" width={16} />
                )
              }
            >
              Continue
            </Button>
          </Box>
        </Stack>
      )}
      {error && (
        <Alert severity="error" variant="outlined" sx={{ mt: 1 }}>
          {error}
        </Alert>
      )}
    </Box>
  );
}

DesignProgress.propTypes = {
  job: PropTypes.object,
  conversation: PropTypes.object,
  stage: PropTypes.string,
  failure: PropTypes.object,
};

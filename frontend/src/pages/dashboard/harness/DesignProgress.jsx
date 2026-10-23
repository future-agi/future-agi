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
import {
  continueHarnessDesign,
  useHarnessReleases,
} from "src/api/harness/releases";
import { paths } from "src/routes/paths";

import { DesignDialog } from "./DesignForm";
import { HostedAgentDialog, UseReleaseDialog } from "./ReleaseGallery";
import {
  CONTINUABLE_DESIGN_CODES,
  DESIGN_STEPS,
  designProgress,
  errorMessage,
  shortDigest,
} from "./harnessShared";

const GLYPHS = {
  done: { icon: "solar:check-circle-bold", color: "accent.pass" },
  active: { icon: "solar:record-circle-bold", color: "accent.info" },
  failed: { icon: "solar:close-circle-bold", color: "accent.fail" },
  pending: { icon: "solar:record-circle-linear", color: "text.secondary" },
};

// A design job (environment-releases.md §7.9): the builder's steps, then what the sealed
// environment offers, or Continue for a design that stopped waiting for an answer.
export default function DesignProgress({ job, conversation, stage, failure }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const done = designProgress(conversation);
  const currentIndex = DESIGN_STEPS.findIndex((step) => !done.has(step.key));
  const design = job?.metadata?.design || {};
  const result = job?.metadata?.design_result;
  const completed = stage === "completed";
  const failed = stage === "failed" || stage === "canceled";
  const continuable =
    failed && CONTINUABLE_DESIGN_CODES.includes(failure?.code);
  const { data: releases = [] } = useHarnessReleases({
    enabled: Boolean(result),
  });
  const sealed = releases.find((one) => one.id === result?.environment?.id);
  const [dialog, setDialog] = useState("");
  const [continuing, setContinuing] = useState(false);
  const [error, setError] = useState("");
  const [idempotencyKey] = useState(harnessIdempotencyKey);

  const stateOf = (index) => {
    if (completed || done.has(DESIGN_STEPS[index].key)) return "done";
    if (index === currentIndex) return failed ? "failed" : "active";
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
      navigate(paths.dashboard.simulate.harness.detail(next.job.job_id));
    } catch (requestError) {
      setError(errorMessage(requestError));
      setContinuing(false);
    }
  };

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
      {result && (
        <Stack spacing={1} sx={{ mt: 1.5 }}>
          <Typography variant="body2">
            Sealed <strong>{result.environment.name}</strong> with{" "}
            {result.suite.scenarios} scenarios ·{" "}
            {shortDigest(result.environment.digest)}
          </Typography>
          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
            <Button
              size="small"
              variant="contained"
              disabled={!sealed}
              onClick={() => setDialog("use")}
              startIcon={<Iconify icon="solar:link-circle-linear" width={16} />}
            >
              Use with my agent
            </Button>
            <Button
              size="small"
              variant="outlined"
              disabled={!sealed}
              onClick={() => setDialog("hosted")}
              startIcon={<Iconify icon="solar:cpu-bolt-linear" width={16} />}
            >
              Run our agent
            </Button>
            <Button
              size="small"
              color="inherit"
              disabled={!sealed}
              onClick={() => setDialog("customize")}
              startIcon={
                <Iconify icon="solar:pen-new-square-linear" width={16} />
              }
            >
              Customize again
            </Button>
          </Stack>
        </Stack>
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
      {sealed && dialog === "use" && (
        <UseReleaseDialog open release={sealed} onClose={() => setDialog("")} />
      )}
      {sealed && dialog === "hosted" && (
        <HostedAgentDialog
          open
          release={sealed}
          onClose={() => setDialog("")}
        />
      )}
      {sealed && dialog === "customize" && (
        <DesignDialog open template={sealed} onClose={() => setDialog("")} />
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

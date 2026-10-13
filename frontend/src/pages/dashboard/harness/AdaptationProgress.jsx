import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";

import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";

import {
  ADAPTATION_STAGES,
  BINDING_MODE_LABELS,
  adaptationStage,
  readable,
  shortDigest,
} from "./harnessShared";

const GLYPHS = {
  done: { icon: "solar:check-circle-bold", color: "accent.pass" },
  active: { icon: "solar:record-circle-bold", color: "accent.info" },
  failed: { icon: "solar:close-circle-bold", color: "accent.fail" },
  pending: { icon: "solar:record-circle-linear", color: "text.secondary" },
};

// The adaptation checklist shown on an adaptation job (environment-releases.md §7.5): six
// stages, then a link to the environment it composed.
export default function AdaptationProgress({ job, conversation, stage }) {
  const navigate = useNavigate();
  const current = adaptationStage(conversation);
  const currentIndex = ADAPTATION_STAGES.indexOf(current);
  const result = job?.metadata?.adaptation_result;
  const completed = stage === "completed";
  const failed = stage === "failed" || stage === "canceled";

  const stateOf = (index) => {
    if (completed) return "done";
    if (index < currentIndex) return "done";
    if (index === currentIndex) return failed ? "failed" : "active";
    return "pending";
  };

  return (
    <Box sx={{ mb: 2 }} aria-label="Adaptation progress">
      <Typography variant="subtitle2" sx={{ mb: 1 }}>
        Adapting {job?.metadata?.adaptation?.suite?.name || "the suite"} to your
        agent
      </Typography>
      <Stack spacing={0.75}>
        {ADAPTATION_STAGES.map((name, index) => {
          const state = stateOf(index);
          return (
            <Stack key={name} direction="row" spacing={1} alignItems="center">
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
                {readable(name)}
              </Typography>
            </Stack>
          );
        })}
      </Stack>
      {result && (
        <Stack spacing={0.5} sx={{ mt: 1.5 }}>
          <Typography variant="caption" color="text.secondary">
            {BINDING_MODE_LABELS[result.mode] || readable(result.mode || "")}{" "}
            binding {shortDigest(result.binding_digest)} · suite{" "}
            {shortDigest(result.suite_digest)}
          </Typography>
          {/* A conversation_only result is recorded but not composed; say why. */}
          {result.compose?.message && (
            <Typography variant="caption" color="text.secondary">
              Not composed: {result.compose.message}
            </Typography>
          )}
          {result.composition_id && (
            <Box>
              <Button
                size="small"
                variant="contained"
                onClick={() =>
                  navigate(
                    paths.dashboard.simulate.environments.detail(
                      result.composition_id,
                    ),
                  )
                }
                endIcon={<Iconify icon="solar:arrow-right-linear" width={16} />}
              >
                Open environment
              </Button>
            </Box>
          )}
        </Stack>
      )}
    </Box>
  );
}

AdaptationProgress.propTypes = {
  job: PropTypes.object,
  conversation: PropTypes.object,
  stage: PropTypes.string,
};

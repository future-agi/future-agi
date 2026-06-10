import { Box } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import PropTypes from "prop-types";
import {
  useApplyTrialPrompt,
  useOptimizeTrialPrompts,
} from "src/api/tests/testDetails";
import { enqueueSnackbar } from "src/components/snackbar";
import PromptDiffView from "./PromptDiffView";
import PromptPanel from "./PromptPanel";

// The apply endpoint picks the right edge per run and reports which one it
// took: a new default PromptVersion (prompt-template runs), a live write to
// the hosted provider agent, or a new active AgentVersion (self-hosted).
const applySuccessMessage = (result) => {
  if (result?.newPromptVersionId) {
    return `Applied as prompt version ${result.templateVersion ?? ""}`.trim();
  }
  if (result?.target === "provider_agent") {
    const fields = (result.appliedFields || []).join(", ");
    return `Applied to the live ${result.provider} agent${fields ? ` (${fields})` : ""}`;
  }
  if (result?.target === "agent_version") {
    return `Applied as agent version v${result.versionNumber} (${result.provider})`;
  }
  return "Fix applied";
};

const PromptDetails = ({ optimizationId, trialId, showDiff }) => {
  const { data: trailPromptData } = useOptimizeTrialPrompts({
    optimizationId,
    trialId,
  });

  const { mutate: applyTrial, isPending: isApplying } = useApplyTrialPrompt({
    onSuccess: (response) => {
      enqueueSnackbar(applySuccessMessage(response?.data?.result), {
        variant: "success",
      });
    },
    onError: (error) => {
      enqueueSnackbar(
        error?.response?.data?.detail || "Failed to apply the fix",
        { variant: "error" },
      );
    },
  });

  const applyButton = (
    <Box sx={{ display: "flex", justifyContent: "flex-end", px: 2, pt: 1 }}>
      <LoadingButton
        size="small"
        variant="contained"
        loading={isApplying}
        onClick={() => applyTrial({ optimizationId, trialId })}
      >
        Apply fix
      </LoadingButton>
    </Box>
  );

  if (showDiff) {
    return (
      <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
        {applyButton}
        <PromptDiffView
          originalPrompt={trailPromptData?.basePrompt}
          optimizedPrompt={trailPromptData?.trialPrompt}
        />
      </Box>
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {applyButton}
      <Box sx={{ display: "flex", gap: 1, flex: 1, minHeight: 0 }}>
        <Box sx={{ flex: 1 }}>
          <PromptPanel
            title="OPTIMIZED AGENT PROMPT"
            prompt={trailPromptData?.trialPrompt}
          />
        </Box>
      </Box>
    </Box>
  );
};

PromptDetails.propTypes = {
  optimizationId: PropTypes.string,
  trialId: PropTypes.string,
  showDiff: PropTypes.bool,
};

export default PromptDetails;

import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useRetryBuild } from "src/api/simulate-environments/retryBuild";
import { failureHeadline } from "../buildEnvironment/failureWords";
import ViewerLock from "../components/ViewerLock";
import useSimulationAccess from "../hooks/useSimulationAccess";
import { refusalText } from "./evals/refusalText";
import { BUILD_STOPPED_COPY as C, WORKSPACE_COPY } from "./workspace.constants";

const MONO = "ui-monospace, Menlo, monospace";

function sentenceOf(failure) {
  const headline = failureHeadline(failure);
  if (headline) return headline;
  if (failure?.domain === "agent") return C.agent;
  if (failure?.domain === "infrastructure") return C.ours;
  return C.other;
}

function detailsOf(failure) {
  if (!failure) return "";
  return [failure.stage, failure.code, failure.message]
    .filter(Boolean)
    .join("\n");
}

export default function BuildStoppedStrip({ envId, failure, retryFrom }) {
  const [open, setOpen] = useState(false);
  const { canEdit } = useSimulationAccess();
  const retry = useRetryBuild(envId);
  const details = detailsOf(failure);
  const onRetry = () =>
    retry.mutate(undefined, {
      onError: (error) =>
        enqueueSnackbar(refusalText(error, C.retryFailed), {
          variant: "error",
        }),
    });
  return (
    <Box
      sx={{
        mx: 2,
        mt: 2,
        px: 2,
        py: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Box
          sx={{
            px: 0.75,
            py: 0.125,
            borderRadius: "4px",
            border: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Typography sx={{ typography: "s3", fontWeight: 600 }}>
            {C.badge}
          </Typography>
        </Box>
        <Typography
          sx={{ typography: "s1", color: "text.primary", flex: 1, minWidth: 0 }}
        >
          {sentenceOf(failure)}
        </Typography>
        {details && (
          <Button
            size="small"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          >
            {open ? C.hideDetails : C.showDetails}
          </Button>
        )}
        {retryFrom && (
          <ViewerLock locked={!canEdit} title={WORKSPACE_COPY.viewer.retry}>
            <Tooltip arrow title={canEdit ? C.retryTip[retryFrom] : ""}>
              <span>
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  disabled={!canEdit || retry.isPending}
                  onClick={onRetry}
                  sx={{ flexShrink: 0 }}
                >
                  {C.retry[retryFrom]}
                </Button>
              </span>
            </Tooltip>
          </ViewerLock>
        )}
      </Stack>
      {open && details && (
        <Typography
          component="pre"
          sx={{
            typography: "s3",
            fontFamily: MONO,
            color: "text.secondary",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            m: 0,
            mt: 1.25,
          }}
        >
          {details}
        </Typography>
      )}
    </Box>
  );
}

BuildStoppedStrip.propTypes = {
  envId: PropTypes.string.isRequired,
  failure: PropTypes.shape({
    domain: PropTypes.string,
    stage: PropTypes.string,
    code: PropTypes.string,
    message: PropTypes.string,
  }),
  retryFrom: PropTypes.oneOf(["start", "checks"]),
};

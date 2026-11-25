import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Collapse,
  Link,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { useRetryBuild } from "src/api/simulate-environments/retryBuild";
import { CONNECTED_AGENTS_PATH } from "src/sections/agent-playground/agentsTabs";
import ViewerLock from "../components/ViewerLock";
import useSimulationAccess from "../hooks/useSimulationAccess";
import { refusalText } from "./evals/refusalText";
import BuildPhaseTable from "./BuildPhaseTable";
import {
  BUILD_STEP_TOTAL,
  keptLine,
  phaseRows,
  spentLine,
  stopCase,
  stopSentence,
} from "./buildSteps";
import { BUILD_STOPPED_COPY as C, WORKSPACE_COPY } from "./workspace.constants";

const MONO = "ui-monospace, Menlo, monospace";

const linkSx = {
  typography: "s3",
  fontWeight: 600,
  color: "text.primary",
  verticalAlign: "baseline",
};

function logOf(kind, failure) {
  if (kind === "canceled") return C.canceledLog;
  return [failure?.stage, failure?.code, failure?.message]
    .filter(Boolean)
    .join("\n");
}

export default function BuildStoppedStrip({
  envId,
  status,
  step,
  counts,
  credits,
  readsAgent,
  designApproved,
  agentId,
}) {
  const [open, setOpen] = useState(false);
  const { canEdit } = useSimulationAccess();
  const retry = useRetryBuild(envId);
  const kind = stopCase(status, step);
  const failure = status?.failure;
  const retryFrom = status?.retry_from || null;
  const log = logOf(kind, failure);
  const spent = spentLine(credits);
  const onRetry = () =>
    retry.mutate(undefined, {
      onError: (error) =>
        enqueueSnackbar(refusalText(error, C.retryFailed), {
          variant: "error",
        }),
    });
  const retryLabel =
    retryFrom === "checks" ? C.retry.checks : C.retry.start(readsAgent);
  const retryTip =
    retryFrom === "checks" ? C.retryTip.checks : C.retryTip.start(readsAgent);
  const secondarySx = {
    color: "text.primary",
    borderColor: "divider",
    flexShrink: 0,
  };
  return (
    <Box sx={{ px: 2, pt: 1.5, flexShrink: 0 }}>
      <Box
        data-testid="build-stop-strip"
        data-case={kind}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          bgcolor: "background.paper",
          overflow: "hidden",
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          gap={1.5}
          sx={{ px: 2, py: 1.25 }}
        >
          <Box sx={{ flexShrink: 0, alignSelf: "flex-start", pt: "1px" }}>
            <StatusBadge
              status="stopped"
              role="neutral"
              variant="badge"
              label={C.badge}
            />
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography sx={{ typography: "s2", color: "text.primary" }}>
              {stopSentence(status, step)}
            </Typography>
            <Typography
              component="div"
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
            >
              {[keptLine(step, counts, designApproved), spent]
                .filter(Boolean)
                .join(" ")}
              {log && (
                <>
                  {" · "}
                  <Link
                    component="button"
                    type="button"
                    underline="hover"
                    aria-expanded={open}
                    onClick={() => setOpen((value) => !value)}
                    sx={linkSx}
                  >
                    {open ? C.hideDetails : C.showDetails}
                  </Link>
                </>
              )}
            </Typography>
          </Box>
          {kind === "checks" && agentId && (
            <Button
              size="small"
              variant="outlined"
              href={`${CONNECTED_AGENTS_PATH}/${agentId}`}
              sx={secondarySx}
            >
              {C.checkConnection}
            </Button>
          )}
          {retryFrom && (
            <ViewerLock locked={!canEdit} title={WORKSPACE_COPY.viewer.retry}>
              <Tooltip arrow title={canEdit ? retryTip : ""}>
                <span>
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    disabled={!canEdit || retry.isPending}
                    onClick={onRetry}
                    startIcon={
                      <Iconify icon="solar:refresh-linear" width={14} />
                    }
                    sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
                  >
                    {retryLabel}
                  </Button>
                </span>
              </Tooltip>
            </ViewerLock>
          )}
        </Stack>

        <BuildPhaseTable
          rows={phaseRows(step, { stopped: true, readsAgent, counts })}
        />

        <Collapse in={open && Boolean(log)} unmountOnExit>
          <Box
            sx={{
              borderTop: "1px solid",
              borderColor: "divider",
              px: 2,
              py: 1.25,
            }}
          >
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mb: 0.75 }}
            >
              {C.logTitle(step.label, step.no, BUILD_STEP_TOTAL)}
            </Typography>
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "6px",
                bgcolor: "background.neutral",
                px: 1.5,
                py: 1,
                maxHeight: 200,
                overflow: "auto",
              }}
            >
              <Typography
                component="pre"
                sx={{
                  typography: "s3",
                  fontFamily: MONO,
                  color: "text.primary",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  m: 0,
                }}
              >
                {log}
              </Typography>
            </Box>
          </Box>
        </Collapse>
      </Box>
    </Box>
  );
}

BuildStoppedStrip.propTypes = {
  envId: PropTypes.string.isRequired,
  status: PropTypes.shape({
    stage: PropTypes.string,
    retry_from: PropTypes.oneOf(["start", "checks", null]),
    failure: PropTypes.shape({
      domain: PropTypes.string,
      stage: PropTypes.string,
      code: PropTypes.string,
      cause: PropTypes.string,
      message: PropTypes.string,
    }),
  }),
  step: PropTypes.shape({
    no: PropTypes.number.isRequired,
    phase: PropTypes.string,
    stage: PropTypes.string,
    label: PropTypes.string.isRequired,
  }).isRequired,
  counts: PropTypes.shape({
    tools: PropTypes.number,
    tables: PropTypes.number,
    scenarios: PropTypes.number,
  }),
  credits: PropTypes.number,
  readsAgent: PropTypes.bool,
  designApproved: PropTypes.bool,
  agentId: PropTypes.string,
};

import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { useMutation } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge";
import { ConfirmDialog } from "src/components/custom-dialog";
import { paths } from "src/routes/paths";
import { cancelHarnessJob } from "src/api/harness/harness";
import {
  errorMessage,
  terminalStages,
} from "src/pages/dashboard/harness/harnessShared";
import ChatSplitPane from "../../components/ChatSplitPane";
import BuilderConsole from "../../buildEnvironment/console/BuilderConsole";
import ViewerLock from "../../components/ViewerLock";
import useSimulationAccess from "../../hooks/useSimulationAccess";
import { WORKSPACE_COPY } from "../workspace.constants";

const titleOf = (name = "") =>
  name.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

function BriefRow({ label, children }) {
  return (
    <Stack
      direction="row"
      spacing={2}
      sx={{ px: 1.5, py: 1, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Typography
        sx={{
          typography: "s1",
          color: "text.secondary",
          width: 120,
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
      <Typography sx={{ typography: "s1", minWidth: 0 }}>{children}</Typography>
    </Stack>
  );
}
BriefRow.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

/**
 * Customize a copy — its design (env-world-customize-design). The builder starts from the
 * ready world and adds the person's change in the chat on the left; nothing is sealed until
 * they approve there. Once it is sealed, the copy is this workspace's own ready environment,
 * used with an agent like any other.
 */
export default function CustomizeTheCopy({ jobId, job, chat }) {
  const navigate = useNavigate();
  const { canEdit } = useSimulationAccess();
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const info = job?.job || {};
  const metadata = info.metadata || {};
  const customization = metadata.customization || {};
  const source = customization.source || {};
  const result = metadata.customization_result || null;
  // The job read carries its state and failure under status, beside the job's details.
  const status = job?.status || {};
  const state = status.state || "queued";
  const finished = terminalStages.has(state);
  // The job stays "provisioning" while the builder designs, which reads as nothing
  // happening: show the conversation's state until the job ends.
  const shownState = finished
    ? state
    : chat?.waiting
      ? "needs_input"
      : chat?.running || chat?.inFlight
        ? "in_progress"
        : state;
  const sourceTitle = titleOf(source.environment_name);
  const cancel = useMutation({
    mutationFn: () => cancelHarnessJob(jobId),
    onSuccess: () => navigate(paths.dashboard.simulate.environments.root),
    onError: (error) =>
      enqueueSnackbar(errorMessage(error), { variant: "error" }),
  });
  const openCopy = () =>
    navigate(
      `${paths.dashboard.simulate.environments.root}?build=${encodeURIComponent(
        `world:${result.environment_release_id}:start`,
      )}`,
    );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <IconButton
          size="small"
          aria-label="Back to environments"
          onClick={() => navigate(paths.dashboard.simulate.environments.root)}
        >
          <Iconify icon="solar:alt-arrow-left-linear" width={16} />
        </IconButton>
        <Typography noWrap sx={{ typography: "m1", minWidth: 0 }}>
          {`${sourceTitle || "Ready environment"} · copy`}
        </Typography>
        <StatusBadge status={shownState} />
        <Box flex={1} />
        {/* A finished copy has nothing left to stop. */}
        {!finished && (
          <ViewerLock locked={!canEdit} title={WORKSPACE_COPY.viewer.cancel}>
            <Button
              variant="outlined"
              color="inherit"
              size="small"
              disabled={cancel.isPending || !canEdit}
              onClick={() => setConfirmingCancel(true)}
              startIcon={<Iconify icon="solar:stop-circle-linear" width={16} />}
            >
              Cancel build
            </Button>
          </ViewerLock>
        )}
      </Stack>
      <ConfirmDialog
        open={confirmingCancel}
        onClose={() => setConfirmingCancel(false)}
        title="Stop building this copy?"
        content="It will be cancelled and no copy will be made. The ready environment stays as it was."
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            disabled={finished}
            onClick={() => {
              setConfirmingCancel(false);
              cancel.mutate();
            }}
          >
            Stop building
          </Button>
        }
      />
      <Box sx={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        <ChatSplitPane
          busy={Boolean(chat.running || chat.inFlight || chat.waiting)}
          chat={({ collapse, open, collapseRef }) => (
            <BuilderConsole
              turns={chat.turns}
              running={chat.running}
              onSend={chat.send}
              onStop={chat.stop}
              canStop={chat.inFlight}
              working={chat.working}
              stopping={chat.stopping}
              frozen={chat.frozen}
              frozenReason={chat.frozenReason}
              answerWithOptions={chat.answerWithOptions}
              onCollapse={collapse}
              collapseRef={collapseRef}
              active={open}
            />
          )}
        >
          <Stack spacing={2} sx={{ p: 2.5, overflowY: "auto", height: "100%" }}>
            <Box>
              <Typography sx={{ typography: "m1" }}>
                The copy&apos;s design
              </Typography>
              <Typography sx={{ typography: "s1", color: "text.secondary" }}>
                The builder starts from the ready world and adds your change.
                Nothing is saved until you approve it in the chat.
              </Typography>
            </Box>
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              <Stack
                direction="row"
                justifyContent="space-between"
                sx={{ px: 1.5, py: 1 }}
              >
                <Typography sx={{ typography: "m3" }}>Brief</Typography>
                <Typography sx={{ typography: "s1", color: "text.secondary" }}>
                  What the copy is built from
                </Typography>
              </Stack>
              <BriefRow label="Description">{customization.change}</BriefRow>
              <BriefRow label="Sources">
                The ready world and your change
              </BriefRow>
              <BriefRow label="Ready world">{sourceTitle}</BriefRow>
              <BriefRow label="Visibility">
                Private to this workspace. Its scores won&apos;t compare with
                the ready environment&apos;s.
              </BriefRow>
            </Box>
            {state === "completed" && result && (
              <Alert
                severity="success"
                action={
                  <Button size="small" color="inherit" onClick={openCopy}>
                    Use with my agent
                  </Button>
                }
              >
                {`Your copy is ready: ${sourceTitle} · copy, ${result.scenario_count} scenarios. It is listed with the ready environments as Private.`}
              </Alert>
            )}
            {state === "failed" && (
              <Alert severity="error">
                {status.failure?.message || "The copy couldn't be built."}
              </Alert>
            )}
            {state === "canceled" && (
              <Alert severity="info">
                This copy was cancelled. The ready environment stays as it was.
              </Alert>
            )}
          </Stack>
        </ChatSplitPane>
      </Box>
    </Box>
  );
}
CustomizeTheCopy.propTypes = {
  jobId: PropTypes.string.isRequired,
  // The copy's job, as the page polls it.
  job: PropTypes.object,
  // useWorkspaceChat's return: the builder's conversation on this copy.
  chat: PropTypes.object.isRequired,
};

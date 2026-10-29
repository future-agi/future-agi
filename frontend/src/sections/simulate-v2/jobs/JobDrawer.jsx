import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import SideDrawer from "../components/SideDrawer";
import { StatusDot } from "../components/primitives";
import CopyCommandButton from "../components/CopyCommandButton";
import { TextLink } from "../agents/connect/connectBits";
import { JOB_DOT, JOB_KINDS, JOB_STATUS, jobCli } from "../_mock/jobs";

const STAGE_DOT = { ...JOB_DOT, done: "passed", pending: "queued" };
const STAGE_LABEL = {
  done: "Done",
  pending: "Not started",
  running: "Running",
  failed: "Failed",
  interrupted: "Interrupted",
  cancelled: "Cancelled",
};

const when = (iso) =>
  iso
    ? new Date(iso).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "—";

/* A titled panel of key-value rows. */
function Panel({ title, children }) {
  return (
    <Box
      sx={{ border: "1px solid", borderColor: "divider", borderRadius: "8px" }}
    >
      <Typography
        sx={{
          px: 1.5,
          py: 1,
          typography: "s2",
          fontWeight: 500,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        {title}
      </Typography>
      {children}
    </Box>
  );
}
Panel.propTypes = { title: PropTypes.string, children: PropTypes.node };

function Row({ k, children, dot, sub }) {
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      spacing={1.5}
      sx={{
        px: 1.5,
        py: 0.75,
        "& + &": { borderTop: "1px solid", borderColor: "divider" },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{ width: 170, flexShrink: 0 }}
      >
        {dot && <StatusDot status={dot} size={6} />}
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {k}
        </Typography>
      </Stack>
      <Box flex={1} minWidth={0}>
        <Typography
          sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
        >
          {children}
        </Typography>
        {sub && (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {sub}
          </Typography>
        )}
      </Box>
    </Stack>
  );
}
Row.propTypes = {
  k: PropTypes.string,
  children: PropTypes.node,
  dot: PropTypes.string,
  sub: PropTypes.node,
};

/**
 * One job: its stages, the work it has completed, its checkpoints (complete
 * or partial, restorable or not), and the evidence's lineage back to the
 * package, model and reward. Cancel and resume say what they keep and what
 * restarts before they act.
 */
export default function JobDrawer({
  job,
  envName,
  canAct,
  onClose,
  onCancel,
  onResume,
  onReattach,
  onResubmit,
}) {
  const navigate = useNavigate();
  if (!job) return <SideDrawer open={false} onClose={onClose} />;
  const reattach = job.status === "interrupted" && job.recovery?.stillRunning;
  return (
    <SideDrawer open onClose={onClose} width={640}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1}
          sx={{
            px: 2,
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography noWrap sx={{ typography: "m3", fontWeight: 600 }}>
              {job.name}
            </Typography>
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ mt: 0.25 }}
            >
              <StatusDot status={JOB_DOT[job.status]} size={6} />
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {`${JOB_STATUS[job.status]} · ${JOB_KINDS[job.kind].label} · `}
                <Box
                  component="span"
                  sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}
                >
                  {job.id}
                </Box>
              </Typography>
            </Stack>
          </Box>
          <IconButton size="small" aria-label="Close" onClick={onClose}>
            <Iconify icon="mingcute:close-line" width={18} />
          </IconButton>
        </Stack>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
          <Stack spacing={2}>
            {/* What can be done now — each opens its plan first. */}
            {canAct && (
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                {job.status === "running" && (
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={onCancel}
                    sx={{ color: "text.primary", borderColor: "divider" }}
                  >
                    Cancel at next boundary…
                  </Button>
                )}
                {reattach && (
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    onClick={onReattach}
                  >
                    Reattach
                  </Button>
                )}
                {["interrupted", "cancelled"].includes(job.status) &&
                  !reattach && (
                    <Button
                      size="small"
                      variant="contained"
                      color="primary"
                      onClick={onResume}
                    >
                      Resume…
                    </Button>
                  )}
                <Button
                  size="small"
                  variant="outlined"
                  onClick={onResubmit}
                  sx={{ color: "text.primary", borderColor: "divider" }}
                >
                  Submit again
                </Button>
              </Stack>
            )}

            {job.recovery && job.status === "interrupted" && (
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {job.recovery.stillRunning
                  ? `The worker watching this job was lost at ${when(job.recovery.at)}, but the job is still running — reattach to follow it. Nothing restarts.`
                  : `The worker was lost at ${when(job.recovery.at)} — an infrastructure failure, not the agent's or the environment's. Resume says what continues and what restarts.`}
              </Typography>
            )}

            <Panel title="Stages">
              {job.stages.map((s) => (
                <Row
                  key={s.id}
                  k={s.label}
                  dot={STAGE_DOT[s.status]}
                  sub={s.detail}
                >
                  {STAGE_LABEL[s.status] || s.status}
                </Row>
              ))}
            </Panel>

            <Panel title="Completed work">
              <Row
                k={
                  job.work.unit.charAt(0).toUpperCase() + job.work.unit.slice(1)
                }
              >{`${job.work.done} of ${job.work.total}`}</Row>
              {job.heldOut && (
                <Row k="Held-out evaluations">{`${job.heldOut.done} of ${job.heldOut.total}`}</Row>
              )}
              <Row
                k="Submitted"
                sub={`Idempotency key ${job.idempotencyKey} — submitting again returns this job`}
              >
                {`${when(job.submittedAt)} · ${job.by} · ${job.client}`}
              </Row>
              {job.finishedAt && <Row k="Finished">{when(job.finishedAt)}</Row>}
            </Panel>

            <Panel title="Checkpoints">
              {job.checkpoints.length ? (
                job.checkpoints.map((c) => (
                  <Row
                    key={c.id}
                    k={c.id}
                    sub={
                      c.note ||
                      (c.restorable
                        ? "Model state saved — a resume restores it."
                        : null)
                    }
                  >
                    {`${c.kind === "partial" ? "Partial" : "Complete"} · step ${c.step} · ${c.restorable ? "restorable" : "not restorable"} · ${when(c.at)}`}
                  </Row>
                ))
              ) : (
                <Row k="None">
                  {job.restoreBlocked
                    ? `${job.restoreBlocked}.`
                    : "This job saves no checkpoints."}
                </Row>
              )}
            </Panel>

            {/* Where the retained evidence came from. */}
            <Panel title="Evidence lineage">
              <Row k="Package">
                {job.lineage.package}
                {" · "}
                <TextLink
                  onClick={() =>
                    navigate(
                      paths.dashboard.simulate.environmentStep(
                        job.envId,
                        "overview",
                      ),
                    )
                  }
                >
                  {envName || "Open environment"}
                </TextLink>
              </Row>
              <Row k="Model">{job.lineage.model}</Row>
              <Row k="Reward">{job.lineage.reward}</Row>
            </Panel>

            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              <CopyCommandButton
                command={jobCli.status(job)}
                label="Copy status command"
              />
              <CopyCommandButton
                command={jobCli.submit(job)}
                label="Copy submit command"
              />
              {["interrupted", "cancelled"].includes(job.status) && (
                <CopyCommandButton
                  command={jobCli.resume(job)}
                  label="Copy resume command"
                />
              )}
            </Stack>
          </Stack>
        </Box>
      </Stack>
    </SideDrawer>
  );
}
JobDrawer.propTypes = {
  job: PropTypes.object,
  envName: PropTypes.string,
  canAct: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  onCancel: PropTypes.func,
  onResume: PropTypes.func,
  onReattach: PropTypes.func,
  onResubmit: PropTypes.func,
};

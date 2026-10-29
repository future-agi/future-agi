import { useMemo, useState } from "react";
import { useSnackbar } from "notistack";
import {
  Box,
  Button,
  MenuItem,
  Select,
  Stack,
  Tab,
  Typography,
} from "@mui/material";
import PageHeader from "src/components/page-header";
import { DataTable } from "src/components/data-table";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useSimStore } from "../store";
import { StatusDot } from "../components/primitives";
import { useRoleGates } from "../_mock/previewRole";
import { envOf } from "../_mock/runGroup";
import {
  JOB_DOT,
  JOB_KINDS,
  JOB_STATUS,
  cancelPlanOf,
  currentStageOf,
  jobsOf,
  resumePlanOf,
} from "../_mock/jobs";
import JobDrawer from "./JobDrawer";
import PropTypes from "prop-types";

const ROW_HEIGHT = 48;
const HEADER_HEIGHT = 40;

/* A plan's lines, one per row. */
function PlanList({ title, lines }) {
  if (!lines.length) return null;
  return (
    <Box>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.tertiary",
          mb: 0.5,
        }}
      >
        {title}
      </Typography>
      <Box
        sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
      >
        {lines.map((l) => (
          <Typography
            key={l}
            sx={{
              px: 1.5,
              py: 0.75,
              typography: "s2",
              "& + &": { borderTop: "1px solid", borderColor: "divider" },
            }}
          >
            {l}
          </Typography>
        ))}
      </Box>
    </Box>
  );
}

PlanList.propTypes = { title: PropTypes.string, lines: PropTypes.array };

/**
 * Jobs (roadmap runs.md, Week 3): package validation and training, each with
 * its stage, status, completed work and a stable job ID — a tab on the
 * Environments page. A job opens in a drawer; cancel and resume show what
 * they keep and what restarts before they act; submitting again returns the
 * same job. Improvements keep their own page.
 */
export default function JobsPage({ embedded = false }) {
  const { state, dispatch } = useSimStore();
  const { enqueueSnackbar } = useSnackbar();
  const gates = useRoleGates();
  const [kind, setKind] = useState("all");
  const [envId, setEnvId] = useState("all");
  const [openId, setOpenId] = useState(null);
  const [ask, setAsk] = useState(null); // { kind: "cancel" | "resume", job }
  const jobs = useMemo(() => jobsOf(state), [state]);
  const envIds = [...new Set(jobs.map((j) => j.envId))];
  const envName = (id) => envOf(state, id)?.name || id;
  const shown = jobs.filter(
    (j) =>
      (kind === "all" || j.kind === kind) &&
      (envId === "all" || j.envId === envId),
  );
  const open = jobs.find((j) => j.id === openId) || null;

  const setJob = (id, patch) =>
    dispatch({
      type: "setWorkspaceSetting",
      key: "jobs",
      value: {
        ...(state.workspaceSettings?.jobs || {}),
        [id]: { ...(state.workspaceSettings?.jobs?.[id] || {}), ...patch },
      },
    });

  const columns = [
    {
      id: "name",
      header: "Job",
      meta: { flex: 2 },
      minSize: 280,
      enableSorting: false,
      cell: ({ row: { original: j } }) => (
        <Box minWidth={0}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
            {j.name}
          </Typography>
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.tertiary",
              fontFamily: "ui-monospace, Menlo, monospace",
            }}
          >
            {j.id}
          </Typography>
        </Box>
      ),
    },
    {
      id: "env",
      header: "Environment",
      meta: { flex: 1.2 },
      minSize: 180,
      enableSorting: false,
      cell: ({ row: { original: j } }) => (
        <Typography noWrap sx={{ typography: "s2" }}>
          {envName(j.envId)}
        </Typography>
      ),
    },
    {
      id: "kind",
      header: "Kind",
      meta: { flex: 1 },
      minSize: 150,
      enableSorting: false,
      cell: ({ row: { original: j } }) => (
        <Typography noWrap sx={{ typography: "s2" }}>
          {JOB_KINDS[j.kind].label}
        </Typography>
      ),
    },
    {
      id: "status",
      header: "Status",
      meta: { flex: 0.9 },
      minSize: 130,
      enableSorting: false,
      cell: ({ row: { original: j } }) => (
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <StatusDot status={JOB_DOT[j.status]} size={6} />
          <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>
            {JOB_STATUS[j.status]}
          </Typography>
        </Stack>
      ),
    },
    {
      id: "stage",
      header: "Stage",
      meta: { flex: 1.1 },
      minSize: 170,
      enableSorting: false,
      cell: ({ row: { original: j } }) => {
        const s = currentStageOf(j);
        const i = j.stages.indexOf(s);
        return (
          <Typography
            noWrap
            sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
          >
            {`${s.label} · ${i + 1} of ${j.stages.length}`}
          </Typography>
        );
      },
    },
    {
      id: "work",
      header: "Completed",
      meta: { flex: 1 },
      minSize: 160,
      enableSorting: false,
      cell: ({ row: { original: j } }) => (
        <Typography
          noWrap
          sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
        >
          {`${j.work.done} of ${j.work.total} ${j.work.unit}`}
        </Typography>
      ),
    },
    {
      id: "submitted",
      header: "Submitted",
      meta: { flex: 1 },
      minSize: 150,
      enableSorting: false,
      cell: ({ row: { original: j } }) => (
        <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>
          {`${new Date(j.submittedAt).toLocaleDateString("en-US", { month: "short", day: "numeric" })} · ${j.client}`}
        </Typography>
      ),
    },
  ];

  const cancelPlan = ask?.kind === "cancel" ? cancelPlanOf(ask.job) : null;
  const resumePlan = ask?.kind === "resume" ? resumePlanOf(ask.job) : null;

  return (
    <Box
      sx={{
        flex: embedded ? 1 : undefined,
        height: embedded ? "auto" : "100%",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        overflow: "hidden",
        minHeight: 0,
        ...(embedded ? { px: 3, py: 2 } : { p: 2 }),
      }}
    >
      {!embedded && (
        <PageHeader
          sx={{ mb: 0, flexShrink: 0 }}
          title="Jobs"
          description="Package validation and training — each with its stage, its checkpoints and where its evidence came from."
        />
      )}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ flexShrink: 0 }}
      >
        <SegmentedTabs value={kind} onChange={(_, v) => setKind(v)}>
          <Tab value="all" label={`All ${jobs.length}`} />
          {Object.entries(JOB_KINDS).map(([id, k]) => (
            <Tab
              key={id}
              value={id}
              label={`${k.label} ${jobs.filter((j) => j.kind === id).length}`}
            />
          ))}
        </SegmentedTabs>
        <Select
          size="small"
          value={envId}
          onChange={(e) => setEnvId(e.target.value)}
          inputProps={{ "aria-label": "Environment" }}
          sx={{
            typography: "s2",
            minWidth: 200,
            "& .MuiSelect-select": { py: 0.5 },
          }}
        >
          <MenuItem value="all" sx={{ typography: "s2" }}>
            All environments
          </MenuItem>
          {envIds.map((id) => (
            <MenuItem key={id} value={id} sx={{ typography: "s2" }}>
              {envName(id)}
            </MenuItem>
          ))}
        </Select>
      </Stack>
      <Box sx={{ flex: 1, minHeight: 0, display: "flex" }}>
        <DataTable
          columns={columns}
          data={shown}
          rowCount={shown.length}
          getRowId={(r) => r.id}
          onRowClick={(r) => setOpenId(r.id)}
          rowHeight={ROW_HEIGHT}
          headerHeight={HEADER_HEIGHT}
          emptyMessage="No jobs yet. Validating a package or starting training adds one here."
        />
      </Box>

      <JobDrawer
        job={ask ? null : open}
        envName={open ? envOf(state, open.envId)?.name : null}
        canAct={gates.canRun}
        onClose={() => setOpenId(null)}
        onCancel={() => setAsk({ kind: "cancel", job: open })}
        onResume={() => setAsk({ kind: "resume", job: open })}
        onReattach={() => {
          setJob(open.id, { status: "running" });
          enqueueSnackbar(
            `Reattached to ${open.id} — it kept running; nothing restarted`,
            { variant: "success" },
          );
        }}
        onResubmit={() =>
          enqueueSnackbar(
            `Same idempotency key — returned the existing job ${open.id}. Nothing new started.`,
            { variant: "info" },
          )
        }
      />

      <ConfirmDialog
        open={!!ask}
        onClose={() => setAsk(null)}
        PaperProps={{ sx: { maxWidth: 560 } }}
        title={
          ask?.kind === "cancel"
            ? `Cancel ${ask?.job?.id}`
            : `Resume ${ask?.job?.id}`
        }
        content={
          <Stack spacing={1.5}>
            {cancelPlan && (
              <>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  {`It stops at ${cancelPlan.boundary}. ${cancelPlan.lost}`}
                </Typography>
                <PlanList title="Kept" lines={cancelPlan.kept} />
              </>
            )}
            {resumePlan && (
              <>
                <PlanList
                  title="Restored"
                  lines={[resumePlan.restore].filter(Boolean)}
                />
                {resumePlan.noRestore && (
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >{`Nothing is restored — ${resumePlan.noRestore.charAt(0).toLowerCase()}${resumePlan.noRestore.slice(1)}`}</Typography>
                )}
                <PlanList
                  title="Restarts"
                  lines={[resumePlan.restart].filter(Boolean)}
                />
                <PlanList title="Kept" lines={[resumePlan.kept]} />
              </>
            )}
          </Stack>
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => {
              const { job } = ask;
              setAsk(null);
              if (ask.kind === "cancel") {
                setJob(job.id, {
                  status: "cancelled",
                  finishedAt: new Date().toISOString(),
                });
                enqueueSnackbar(
                  `${job.id} cancelled at the next boundary — completed work kept`,
                  { variant: "success" },
                );
              } else {
                setJob(job.id, { status: "running", finishedAt: null });
                enqueueSnackbar(`${job.id} resumed — same job ID`, {
                  variant: "success",
                });
              }
            }}
          >
            {ask?.kind === "cancel" ? "Cancel job" : "Resume job"}
          </Button>
        }
      />
    </Box>
  );
}

JobsPage.propTypes = {
  /* Inside the Environments page's Jobs tab: no page header of its own. */
  embedded: PropTypes.bool,
};
